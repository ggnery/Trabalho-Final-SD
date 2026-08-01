"""Histórico durável de mensagens, sobre DynamoDB.

Modelagem
---------
A tabela usa ``room_id`` como partition key e ``seq`` como **sort key**. Essa
escolha não é decorativa: ela faz o replay de reconexão — a operação que
garante zero perda de mensagens quando um nó cai — ser uma única ``Query`` com
``seq > :last``, já devolvida em ordem crescente pelo próprio índice. Nenhuma
ordenação em memória, nenhum ``Scan``, custo proporcional apenas ao que o
cliente de fato perdeu.

Se ``seq`` fosse um atributo comum em vez de sort key, a mesma recuperação
exigiria varrer a partição inteira e ordenar na aplicação.

Falha degrada, não derruba
--------------------------
Nenhum método aqui está no caminho crítico de entrega (ADR-008). Se o DynamoDB
estiver indisponível, o chat continua funcionando em tempo real via Pub/Sub;
apenas o replay de histórico fica degradado. Por isso os erros são registrados
e engolidos, em vez de propagados até derrubar a conexão do usuário.
"""

from __future__ import annotations

import time
from typing import Any

import aioboto3
import structlog
from botocore.exceptions import BotoCoreError, ClientError

from salaviva.domain.models import MessageEnvelope

__all__ = ["DynamoMessageRepository", "NullMessageRepository"]

log = structlog.get_logger(__name__)


class DynamoMessageRepository:
    """Adaptador de :class:`salaviva.ports.MessageRepository`."""

    def __init__(
        self,
        table_name: str,
        region: str = "us-east-1",
        endpoint_url: str | None = None,
        ttl_seconds: int = 7 * 86_400,
    ) -> None:
        self._table_name = table_name
        self._region = region
        self._endpoint_url = endpoint_url
        self._ttl_seconds = ttl_seconds
        self._session = aioboto3.Session()
        self._healthy = True

    def _client(self):
        return self._session.client(
            "dynamodb",
            region_name=self._region,
            endpoint_url=self._endpoint_url,
        )

    async def save(self, envelope: MessageEnvelope) -> None:
        item: dict[str, Any] = {
            "room_id": {"S": envelope.room_id},
            "seq": {"N": str(envelope.seq)},
            "message_id": {"S": envelope.message_id},
            "client_msg_id": {"S": envelope.client_msg_id},
            "sender": {"S": envelope.sender},
            "session_id": {"S": envelope.session_id},
            "content": {"S": envelope.content},
            "lamport": {"N": str(envelope.lamport)},
            "vector_clock": {"M": {k: {"N": str(v)} for k, v in envelope.vector_clock.items()}},
            "node_id": {"S": envelope.node_id},
            "ts": {"S": envelope.ts},
            "ttl": {"N": str(int(time.time()) + self._ttl_seconds)},
        }
        try:
            async with self._client() as ddb:
                await ddb.put_item(
                    TableName=self._table_name,
                    Item=item,
                    # Não sobrescreve: se o mesmo (room_id, seq) já existe, algo
                    # violou a unicidade do sequenciador e queremos saber disso
                    # em vez de perder silenciosamente a mensagem original.
                    ConditionExpression=(
                        "attribute_not_exists(room_id) AND attribute_not_exists(seq)"
                    ),
                )
            self._healthy = True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                log.warning("seq_duplicado_ignorado", room_id=envelope.room_id, seq=envelope.seq)
                return
            self._healthy = False
            log.error("dynamo_put_falhou", error=str(exc), room_id=envelope.room_id)
        except (BotoCoreError, OSError) as exc:
            self._healthy = False
            log.error("dynamo_indisponivel", error=str(exc))

    async def backlog(
        self, room_id: str, after_seq: int = 0, limit: int = 200
    ) -> list[MessageEnvelope]:
        try:
            async with self._client() as ddb:
                resp = await ddb.query(
                    TableName=self._table_name,
                    KeyConditionExpression="room_id = :r AND seq > :s",
                    ExpressionAttributeValues={
                        ":r": {"S": room_id},
                        ":s": {"N": str(after_seq)},
                    },
                    ScanIndexForward=True,  # ordem crescente de seq
                    Limit=limit,
                    # Leitura forte: o cliente que reconecta precisa enxergar a
                    # última escrita, senão a recuperação perde justamente a
                    # mensagem que motivou a reconexão.
                    ConsistentRead=True,
                )
            self._healthy = True
            return [self._to_envelope(i) for i in resp.get("Items", [])]
        except (ClientError, BotoCoreError, OSError) as exc:
            self._healthy = False
            log.error("dynamo_query_falhou", error=str(exc), room_id=room_id)
            return []

    async def healthy(self) -> bool:
        return self._healthy

    async def ensure_tables(self) -> None:
        """Cria a tabela se não existir. Usado no Compose local com DynamoDB Local.

        Na AWS a tabela é criada pelo Terraform — infraestrutura não deve ser
        criada por código de aplicação em produção.
        """
        try:
            async with self._client() as ddb:
                existing = await ddb.list_tables()
                if self._table_name in existing.get("TableNames", []):
                    return
                await ddb.create_table(
                    TableName=self._table_name,
                    KeySchema=[
                        {"AttributeName": "room_id", "KeyType": "HASH"},
                        {"AttributeName": "seq", "KeyType": "RANGE"},
                    ],
                    AttributeDefinitions=[
                        {"AttributeName": "room_id", "AttributeType": "S"},
                        {"AttributeName": "seq", "AttributeType": "N"},
                    ],
                    BillingMode="PAY_PER_REQUEST",
                )
                log.info("tabela_criada", table=self._table_name)
        except (ClientError, BotoCoreError, OSError) as exc:
            log.warning("ensure_tables_falhou", error=str(exc))

    @staticmethod
    def _to_envelope(item: dict[str, Any]) -> MessageEnvelope:
        return MessageEnvelope(
            message_id=item["message_id"]["S"],
            client_msg_id=item["client_msg_id"]["S"],
            room_id=item["room_id"]["S"],
            sender=item["sender"]["S"],
            session_id=item["session_id"]["S"],
            content=item["content"]["S"],
            seq=int(item["seq"]["N"]),
            lamport=int(item["lamport"]["N"]),
            vector_clock={
                k: int(v["N"]) for k, v in item.get("vector_clock", {}).get("M", {}).items()
            },
            node_id=item["node_id"]["S"],
            ts=item["ts"]["S"],
        )


class NullMessageRepository:
    """Repositório que não persiste nada.

    Usado quando ``SALAVIVA_PERSISTENCE_ENABLED=false`` — no Compose sem
    credenciais AWS e no plano B da apresentação. O chat funciona integralmente
    em tempo real; o que se perde é o replay de histórico na reconexão, e o
    ``/readyz`` continua aprovando porque a ausência de persistência é uma
    configuração deliberada, não uma falha.
    """

    async def save(self, envelope: MessageEnvelope) -> None:
        return None

    async def backlog(
        self, room_id: str, after_seq: int = 0, limit: int = 200
    ) -> list[MessageEnvelope]:
        return []

    async def healthy(self) -> bool:
        return True
