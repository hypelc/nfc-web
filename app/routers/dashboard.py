import io
import secrets
from datetime import datetime, timezone
from urllib.parse import urlparse

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.auth import obter_usuario_atual
from app.config import PUBLIC_BASE_URL
from app.database import abrir_conexao


router = APIRouter()

ESTATISTICAS_TIMEZONE = "America/Sao_Paulo"


def _obter_referencia_estatisticas() -> datetime:
    """Retorna uma única referência UTC para todos os cálculos do painel."""

    return datetime.now(timezone.utc)


def _consultar_serie_temporal(cursor, establishment_id: int, referencia: datetime):
    """Consulta os 30 dias locais mais recentes, preenchendo dias vazios."""

    cursor.execute(
        f"""
        WITH parametros AS (
            SELECT
                e.id AS establishment_id,
                e.stats_start_at,
                (
                    (%s::timestamptz AT TIME ZONE '{ESTATISTICAS_TIMEZONE}')::date
                ) AS hoje_local
            FROM establishments e
            WHERE e.id = %s
        ), dias AS (
            SELECT
                (p.hoje_local - deslocamento.n)::date AS dia,
                p.establishment_id,
                p.stats_start_at
            FROM parametros p
            CROSS JOIN generate_series(0, 29) AS deslocamento(n)
        )
        SELECT
            d.dia,
            COUNT(ae.id) AS acessos
        FROM dias d
        LEFT JOIN qr_codes qc
            ON qc.establishment_id = d.establishment_id
        LEFT JOIN access_events ae
            ON ae.qr_code_id = qc.id
            AND ae.accessed_at >= d.stats_start_at
            AND ae.accessed_at >= (
                d.dia::timestamp AT TIME ZONE '{ESTATISTICAS_TIMEZONE}'
            )
            AND ae.accessed_at < (
                (d.dia + 1)::timestamp AT TIME ZONE '{ESTATISTICAS_TIMEZONE}'
            )
        GROUP BY d.dia
        ORDER BY d.dia
        """,
        (referencia, establishment_id),
    )
    return cursor.fetchall()


class CadastroEmpresaRequest(BaseModel):
    nome: str = Field(min_length=2, max_length=120)
    link_avaliacao: str = Field(min_length=8, max_length=2048)

    @field_validator("nome")
    @classmethod
    def validar_nome(cls, valor: str) -> str:
        valor = valor.strip()

        if not valor:
            raise ValueError("O nome da empresa não pode ficar vazio")

        return valor

    @field_validator("link_avaliacao")
    @classmethod
    def validar_link(cls, valor: str) -> str:
        valor = valor.strip()
        url = urlparse(valor)

        if url.scheme not in ("http", "https") or not url.netloc:
            raise ValueError("Informe uma URL válida começando com http:// ou https://")

        return valor


class AtualizarNomeEmpresaRequest(BaseModel):
    nome: str = Field(min_length=2, max_length=120)

    @field_validator("nome")
    @classmethod
    def validar_nome(cls, valor: str) -> str:
        valor = valor.strip()

        if not valor:
            raise ValueError("O nome da empresa não pode ficar vazio")

        return valor


class AtualizarDestinoRequest(BaseModel):
    link_avaliacao: str = Field(min_length=8, max_length=2048)

    @field_validator("link_avaliacao")
    @classmethod
    def validar_link(cls, valor: str) -> str:
        valor = valor.strip()
        url = urlparse(valor)

        if url.scheme not in ("http", "https") or not url.netloc:
            raise ValueError("Informe uma URL válida começando com http:// ou https://")

        return valor


class AtualizarStatusQrRequest(BaseModel):
    ativo: bool


def _obter_conta_dashboard(cursor, usuario_id: str):
    cursor.execute(
        """
        SELECT role, establishment_id
        FROM dashboard_accounts
        WHERE user_id = %s
        """,
        (usuario_id,),
    )
    conta = cursor.fetchone()

    if conta is None:
        raise HTTPException(
            status_code=403,
            detail="Usuário sem permissão para acessar o dashboard",
        )

    return conta


def _exigir_admin(cursor, usuario_id: str):
    role, _ = _obter_conta_dashboard(cursor, usuario_id)

    if role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Apenas administradores podem acessar este painel",
        )


def _registrar_auditoria(
    cursor,
    usuario_id: str,
    acao: str,
    tipo_recurso: str,
    recurso_id: int,
):
    cursor.execute(
        """
        INSERT INTO public.admin_audit_logs (
            actor_user_id,
            action,
            resource_type,
            resource_id
        )
        VALUES (%s, %s, %s, %s)
        """,
        (usuario_id, acao, tipo_recurso, recurso_id),
    )


def _executar_inicio_novo_periodo(establishment_id: int, usuario_id: str):
    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            _exigir_admin(cursor, usuario_id)
            cursor.execute(
                """
                SELECT id, name, stats_start_at, archived_at
                FROM establishments
                WHERE id = %s
                """,
                (establishment_id,),
            )
            empresa = cursor.fetchone()

            if empresa is None:
                raise HTTPException(status_code=404, detail="Empresa não encontrada")

            if empresa[3] is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Não é possível iniciar um período para uma empresa arquivada",
                )

            cursor.execute(
                """
                UPDATE establishments
                SET stats_start_at = NOW()
                WHERE id = %s
                RETURNING id, name, stats_start_at
                """,
                (establishment_id,),
            )
            periodo = cursor.fetchone()
            _registrar_auditoria(
                cursor,
                usuario_id,
                "stats_period_started",
                "establishment",
                establishment_id,
            )

    return {
        "id": periodo[0],
        "nome": periodo[1],
        "periodo_anterior_iniciado_em": empresa[2],
        "novo_periodo_iniciado_em": periodo[2],
    }


@router.post("/admin/establishments", status_code=status.HTTP_201_CREATED)
def cadastrar_empresa(
    dados: CadastroEmpresaRequest,
    usuario: dict = Depends(obter_usuario_atual),
):
    """Cadastra uma empresa e seu primeiro QR Code em uma única transação."""

    codigo_qr = secrets.token_urlsafe(8)

    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            _exigir_admin(cursor, usuario["id"])

            cursor.execute(
                """
                INSERT INTO establishments (name)
                VALUES (%s)
                RETURNING id, name
                """,
                (dados.nome,),
            )
            empresa_id, nome_empresa = cursor.fetchone()

            cursor.execute(
                """
                INSERT INTO qr_codes (establishment_id, code, destination_url)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (empresa_id, codigo_qr, dados.link_avaliacao),
            )
            qr_code_id = cursor.fetchone()[0]

    return {
        "empresa": {
            "id": empresa_id,
            "nome": nome_empresa,
        },
        "qr_code": {
            "id": qr_code_id,
            "codigo": codigo_qr,
            "url_publica": f"{PUBLIC_BASE_URL}/q/{codigo_qr}",
            "destino_url": dados.link_avaliacao,
        },
    }


@router.patch("/admin/establishments/{establishment_id}")
def atualizar_empresa(
    dados: AtualizarNomeEmpresaRequest,
    establishment_id: int = Path(gt=0),
    usuario: dict = Depends(obter_usuario_atual),
):
    """Atualiza o nome de uma empresa cadastrada."""

    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            _exigir_admin(cursor, usuario["id"])
            cursor.execute(
                """
                UPDATE establishments
                SET name = %s
                WHERE id = %s
                RETURNING id, name
                """,
                (dados.nome, establishment_id),
            )
            empresa = cursor.fetchone()

    if empresa is None:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    return {"id": empresa[0], "nome": empresa[1]}


@router.patch("/admin/qr-codes/{codigo}/destination")
def atualizar_destino_qr(
    dados: AtualizarDestinoRequest,
    codigo: str,
    usuario: dict = Depends(obter_usuario_atual),
):
    """Atualiza o destino de um QR Code ativo ou inativo."""

    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            _exigir_admin(cursor, usuario["id"])
            cursor.execute(
                """
                UPDATE qr_codes
                SET destination_url = %s
                WHERE code = %s
                RETURNING id, code, destination_url, is_active
                """,
                (dados.link_avaliacao, codigo),
            )
            qr_code = cursor.fetchone()
            if qr_code is None:
                raise HTTPException(status_code=404, detail="QR Code não encontrado")

            _registrar_auditoria(
                cursor,
                usuario["id"],
                "qr_destination_updated",
                "qr_code",
                qr_code[0],
            )

    if qr_code is None:
        raise HTTPException(status_code=404, detail="QR Code não encontrado")

    return {
        "codigo": qr_code[1],
        "destino_url": qr_code[2],
        "ativo": qr_code[3],
    }


@router.patch("/admin/qr-codes/{codigo}/status")
def atualizar_status_qr(
    dados: AtualizarStatusQrRequest,
    codigo: str,
    usuario: dict = Depends(obter_usuario_atual),
):
    """Ativa ou desativa um QR Code sem apagar seu histórico."""

    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            _exigir_admin(cursor, usuario["id"])
            cursor.execute(
                """
                UPDATE qr_codes
                SET is_active = %s
                WHERE code = %s
                RETURNING id, code, destination_url, is_active
                """,
                (dados.ativo, codigo),
            )
            qr_code = cursor.fetchone()
            if qr_code is None:
                raise HTTPException(status_code=404, detail="QR Code não encontrado")

            _registrar_auditoria(
                cursor,
                usuario["id"],
                "qr_status_updated",
                "qr_code",
                qr_code[0],
            )

    if qr_code is None:
        raise HTTPException(status_code=404, detail="QR Code não encontrado")

    return {
        "codigo": qr_code[1],
        "destino_url": qr_code[2],
        "ativo": qr_code[3],
    }


@router.get("/admin/qr-codes/{codigo}/image")
def obter_imagem_qr(
    codigo: str,
    usuario: dict = Depends(obter_usuario_atual),
):
    """Gera a imagem do QR em memória para o admin visualizar ou baixar."""

    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            _exigir_admin(cursor, usuario["id"])
            cursor.execute(
                "SELECT code FROM qr_codes WHERE code = %s",
                (codigo,),
            )
            qr_code = cursor.fetchone()

    if qr_code is None:
        raise HTTPException(status_code=404, detail="QR Code não encontrado")

    imagem = qrcode.make(f"{PUBLIC_BASE_URL}/q/{qr_code[0]}")
    arquivo = io.BytesIO()
    imagem.save(arquivo, format="PNG")
    arquivo.seek(0)

    return StreamingResponse(
        arquivo,
        media_type="image/png",
        headers={
            "Content-Disposition": f'inline; filename="qr_{qr_code[0]}.png"',
        },
    )


@router.post("/admin/establishments/{establishment_id}/archive")
def arquivar_empresa(
    establishment_id: int = Path(gt=0),
    usuario: dict = Depends(obter_usuario_atual),
):
    """Arquiva uma empresa sem apagar seu cadastro ou histórico."""

    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            _exigir_admin(cursor, usuario["id"])
            cursor.execute(
                """
                SELECT id, name, archived_at
                FROM establishments
                WHERE id = %s
                """,
                (establishment_id,),
            )
            empresa = cursor.fetchone()

            if empresa is None:
                raise HTTPException(status_code=404, detail="Empresa não encontrada")

            if empresa[2] is not None:
                raise HTTPException(status_code=409, detail="Empresa já está arquivada")

            cursor.execute(
                """
                UPDATE establishments
                SET archived_at = NOW()
                WHERE id = %s AND archived_at IS NULL
                RETURNING id, name, archived_at
                """,
                (establishment_id,),
            )
            arquivada = cursor.fetchone()

            if arquivada is None:
                raise HTTPException(status_code=409, detail="Empresa já está arquivada")

            _registrar_auditoria(
                cursor,
                usuario["id"],
                "establishment_archived",
                "establishment",
                establishment_id,
            )

    return {
        "id": arquivada[0],
        "nome": arquivada[1],
        "arquivada_em": arquivada[2],
    }


@router.post("/admin/establishments/{establishment_id}/restore")
def restaurar_empresa(
    establishment_id: int = Path(gt=0),
    usuario: dict = Depends(obter_usuario_atual),
):
    """Restaura uma empresa sem alterar o estado individual dos seus QR Codes."""

    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            _exigir_admin(cursor, usuario["id"])
            cursor.execute(
                """
                SELECT id, name, archived_at
                FROM establishments
                WHERE id = %s
                """,
                (establishment_id,),
            )
            empresa = cursor.fetchone()

            if empresa is None:
                raise HTTPException(status_code=404, detail="Empresa não encontrada")

            if empresa[2] is None:
                raise HTTPException(status_code=409, detail="Empresa já está ativa")

            cursor.execute(
                """
                UPDATE establishments
                SET archived_at = NULL
                WHERE id = %s AND archived_at IS NOT NULL
                RETURNING id, name
                """,
                (establishment_id,),
            )
            restaurada = cursor.fetchone()

            if restaurada is None:
                raise HTTPException(status_code=409, detail="Empresa já está ativa")

            _registrar_auditoria(
                cursor,
                usuario["id"],
                "establishment_restored",
                "establishment",
                establishment_id,
            )

    return {"id": restaurada[0], "nome": restaurada[1]}


@router.post("/admin/establishments/{establishment_id}/start-stats-period")
def iniciar_periodo_estatisticas(
    establishment_id: int = Path(gt=0),
    usuario: dict = Depends(obter_usuario_atual),
):
    """Inicia um período novo preservando os eventos históricos."""

    return _executar_inicio_novo_periodo(establishment_id, usuario["id"])


@router.delete("/admin/establishments/{establishment_id}")
def excluir_empresa(
    establishment_id: int = Path(gt=0),
    usuario: dict = Depends(obter_usuario_atual),
):
    """Recusa a operação antiga para impedir exclusões físicas durante a transição."""

    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            _exigir_admin(cursor, usuario["id"])

    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail="Exclusão permanente desativada; use a operação de arquivamento",
        headers={"Allow": "POST"},
    )

@router.post("/admin/establishments/{establishment_id}/reset-accesses")
def resetar_acessos_empresa(
    establishment_id: int = Path(gt=0),
    usuario: dict = Depends(obter_usuario_atual),
):
    """Mantém compatibilidade sem apagar eventos; usa o novo marco estatístico."""

    return _executar_inicio_novo_periodo(establishment_id, usuario["id"])


@router.get("/establishments")
def listar_estabelecimentos(usuario: dict = Depends(obter_usuario_atual)):
    """Lista as empresas que o usuário autenticado pode visualizar."""

    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            role, client_establishment_id = _obter_conta_dashboard(
                cursor,
                usuario["id"],
            )

            if role == "admin":
                cursor.execute(
                    """
                    SELECT id, name
                    FROM establishments
                    WHERE archived_at IS NULL
                    ORDER BY name
                    """
                )
            else:
                if client_establishment_id is None:
                    raise HTTPException(
                        status_code=403,
                        detail="Cliente sem empresa vinculada",
                    )

                cursor.execute(
                    """
                    SELECT id, name, archived_at
                    FROM establishments
                    WHERE id = %s
                    """,
                    (client_establishment_id,),
                )
                empresa_cliente = cursor.fetchone()

                if empresa_cliente is None:
                    raise HTTPException(status_code=404, detail="Empresa não encontrada")

                if empresa_cliente[2] is not None:
                    raise HTTPException(
                        status_code=403,
                        detail="O acesso desta empresa está temporariamente indisponível",
                    )

                empresas = [empresa_cliente[:2]]

            if role == "admin":
                empresas = cursor.fetchall()

    return {
        "role": role,
        "empresas": [
            {"id": empresa_id, "nome": nome}
            for empresa_id, nome in empresas
        ],
    }


@router.get("/admin/archived-establishments")
def listar_estabelecimentos_arquivados(
    usuario: dict = Depends(obter_usuario_atual),
):
    """Lista empresas arquivadas para restauração administrativa."""

    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            _exigir_admin(cursor, usuario["id"])
            cursor.execute(
                """
                SELECT
                    e.id,
                    e.name,
                    e.archived_at,
                    COUNT(qc.id) AS total_qr_codes
                FROM establishments e
                LEFT JOIN qr_codes qc ON qc.establishment_id = e.id
                WHERE e.archived_at IS NOT NULL
                GROUP BY e.id, e.name, e.archived_at
                ORDER BY e.archived_at DESC, e.name
                """
            )
            empresas = cursor.fetchall()

    return {
        "empresas": [
            {
                "id": empresa_id,
                "nome": nome,
                "arquivada_em": arquivada_em,
                "total_qr_codes": total_qr_codes or 0,
            }
            for empresa_id, nome, arquivada_em, total_qr_codes in empresas
        ]
    }


@router.get("/overview")
def obter_resumo_dashboard(
    establishment_id: int | None = Query(default=None, gt=0),
    usuario: dict = Depends(obter_usuario_atual),
):
    """Retorna o resumo de uma empresa permitida ao usuário."""

    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            cursor.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            role, client_establishment_id = _obter_conta_dashboard(
                cursor,
                usuario["id"],
            )

            if role == "client":
                if client_establishment_id is None:
                    raise HTTPException(
                        status_code=403,
                        detail="Cliente sem empresa vinculada",
                    )

                if (
                    establishment_id is not None
                    and establishment_id != client_establishment_id
                ):
                    raise HTTPException(
                        status_code=403,
                        detail="Cliente não pode acessar outra empresa",
                    )

                establishment_id = client_establishment_id
            elif role == "admin":
                if establishment_id is None:
                    raise HTTPException(
                        status_code=400,
                        detail="Admin precisa informar a empresa",
                    )
            else:
                raise HTTPException(
                    status_code=403,
                    detail="Perfil de dashboard inválido",
                )

            cursor.execute(
                """
                SELECT id, name, stats_start_at, archived_at
                FROM establishments
                WHERE id = %s
                """,
                (establishment_id,),
            )
            establishment = cursor.fetchone()

            if establishment is None:
                raise HTTPException(
                    status_code=404,
                    detail="Empresa não encontrada",
                )

            (
                establishment_id,
                establishment_name,
                stats_start_at,
                archived_at,
            ) = establishment

            if role == "client" and archived_at is not None:
                raise HTTPException(
                    status_code=403,
                    detail="O acesso desta empresa está temporariamente indisponível",
                )

            referencia_estatisticas = _obter_referencia_estatisticas()

            cursor.execute(
                """
                SELECT
                    COUNT(ae.id) AS total_acessos,
                    COUNT(*) FILTER (
                        WHERE ae.source = 'qr'
                    ) AS acessos_qr,
                    COUNT(*) FILTER (
                        WHERE ae.source = 'nfc'
                    ) AS acessos_nfc,
                    MAX(ae.accessed_at) AS ultimo_acesso
                FROM establishments e
                LEFT JOIN qr_codes qc ON qc.establishment_id = e.id
                LEFT JOIN access_events ae
                    ON ae.qr_code_id = qc.id
                    AND ae.accessed_at >= e.stats_start_at
                WHERE e.id = %s
                """,
                (establishment_id,),
            )
            estatisticas = cursor.fetchone()

            serie_bruta = _consultar_serie_temporal(
                cursor,
                establishment_id,
                referencia_estatisticas,
            )

            cursor.execute(
                """
                SELECT code, destination_url, is_active
                FROM qr_codes qc
                WHERE qc.establishment_id = %s
                ORDER BY qc.created_at DESC, qc.id DESC
                LIMIT 1
                """,
                (establishment_id,),
            )
            qr_code = cursor.fetchone()

            cursor.execute(
                """
                SELECT qc.code, ae.source, ae.accessed_at
                FROM access_events ae
                JOIN qr_codes qc ON qc.id = ae.qr_code_id
                JOIN establishments e ON e.id = qc.establishment_id
                WHERE qc.establishment_id = %s
                  AND ae.accessed_at >= e.stats_start_at
                ORDER BY ae.accessed_at DESC
                LIMIT 10
                """,
                (establishment_id,),
            )
            acessos_recentes = cursor.fetchall()

    (
        total_acessos,
        acessos_qr,
        acessos_nfc,
        ultimo_acesso,
    ) = estatisticas

    serie_temporal_30 = [
        {"data": dia, "acessos": acessos or 0}
        for dia, acessos in serie_bruta
    ]
    serie_temporal_7 = serie_temporal_30[-7:]
    acessos_hoje = serie_temporal_30[-1]["acessos"]
    acessos_ultimos_7_dias = sum(
        ponto["acessos"] for ponto in serie_temporal_7
    )

    qr_atual = None
    if qr_code is not None:
        codigo, destino, ativo = qr_code
        qr_atual = {
            "codigo": codigo,
            "destino_url": destino,
            "ativo": ativo,
        }

    return {
        "empresa": {
            "id": establishment_id,
            "nome": establishment_name,
            "arquivada": archived_at is not None,
            "stats_start_at": stats_start_at,
        },
        "estatisticas": {
            "acessos_hoje": acessos_hoje,
            "acessos_ultimos_7_dias": acessos_ultimos_7_dias,
            "total_acessos": total_acessos,
            "acessos_qr": acessos_qr,
            "acessos_nfc": acessos_nfc,
            "ultimo_acesso": ultimo_acesso,
        },
        "serie_temporal": {
            "7_dias": serie_temporal_7,
            "30_dias": serie_temporal_30,
        },
        "qr_atual": qr_atual,
        "acessos_recentes": [
            {
                "codigo": codigo,
                "origem": origem,
                "acessado_em": acessado_em,
            }
            for codigo, origem, acessado_em in acessos_recentes
        ],
    }


@router.get("/admin/overview")
def obter_resumo_admin(usuario: dict = Depends(obter_usuario_atual)):
    """Retorna indicadores gerais para usuários com perfil admin."""

    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            _exigir_admin(cursor, usuario["id"])

            referencia_estatisticas = _obter_referencia_estatisticas()

            cursor.execute(
                f"""
                WITH parametros AS (
                    SELECT (
                        (
                            (
                                (%s::timestamptz AT TIME ZONE '{ESTATISTICAS_TIMEZONE}')::date
                                - 6
                            )::timestamp AT TIME ZONE '{ESTATISTICAS_TIMEZONE}'
                        )
                    ) AS inicio_7_dias
                )
                SELECT
                    e.id,
                    e.name,
                    COUNT(DISTINCT qc.id) AS total_qr_codes,
                    COUNT(DISTINCT qc.id) FILTER (
                        WHERE qc.is_active = TRUE
                    ) AS qr_codes_ativos,
                    COUNT(ae.id) AS total_acessos,
                    COUNT(ae.id) FILTER (
                        WHERE ae.accessed_at >= GREATEST(
                            e.stats_start_at,
                            p.inicio_7_dias
                        )
                    ) AS acessos_ultimos_7_dias,
                    COUNT(ae.id) FILTER (
                        WHERE ae.source = 'qr'
                    ) AS acessos_qr,
                    COUNT(ae.id) FILTER (
                        WHERE ae.source = 'nfc'
                    ) AS acessos_nfc,
                    MAX(ae.accessed_at) AS ultimo_acesso
                FROM establishments e
                LEFT JOIN qr_codes qc ON qc.establishment_id = e.id
                LEFT JOIN access_events ae
                    ON ae.qr_code_id = qc.id
                    AND ae.accessed_at >= e.stats_start_at
                CROSS JOIN parametros p
                WHERE e.archived_at IS NULL
                GROUP BY e.id, e.name, e.stats_start_at
                ORDER BY e.name
                """,
                (referencia_estatisticas,),
            )
            empresas = cursor.fetchall()

            cursor.execute(
                """
                SELECT e.name, qc.code, ae.source, ae.accessed_at
                FROM access_events ae
                JOIN qr_codes qc ON qc.id = ae.qr_code_id
                JOIN establishments e ON e.id = qc.establishment_id
                WHERE e.archived_at IS NULL
                  AND ae.accessed_at >= e.stats_start_at
                ORDER BY ae.accessed_at DESC
                LIMIT 12
            """
            )
            acessos_recentes = cursor.fetchall()

            cursor.execute(
                """
                SELECT qc.id, qc.establishment_id, qc.code, qc.destination_url, qc.is_active
                FROM qr_codes qc
                JOIN establishments e ON e.id = qc.establishment_id
                WHERE e.archived_at IS NULL
                ORDER BY qc.created_at DESC, qc.id DESC
                """
            )
            qr_codes = cursor.fetchall()

    total_qr_codes = sum(empresa[2] or 0 for empresa in empresas)
    qr_codes_ativos = sum(empresa[3] or 0 for empresa in empresas)
    total_acessos = sum(empresa[4] or 0 for empresa in empresas)
    acessos_ultimos_7_dias = sum(empresa[5] or 0 for empresa in empresas)
    acessos_qr = sum(empresa[6] or 0 for empresa in empresas)
    acessos_nfc = sum(empresa[7] or 0 for empresa in empresas)

    qr_codes_por_empresa = {}
    for qr_id, empresa_id, codigo, destino_url, ativo in qr_codes:
        qr_codes_por_empresa.setdefault(empresa_id, []).append(
            {
                "id": qr_id,
                "codigo": codigo,
                "destino_url": destino_url,
                "ativo": ativo,
                "url_publica": f"{PUBLIC_BASE_URL}/q/{codigo}",
            }
        )

    return {
        "indicadores": {
            "total_empresas": len(empresas),
            "total_qr_codes": total_qr_codes,
            "qr_codes_ativos": qr_codes_ativos,
            "total_acessos": total_acessos,
            "acessos_ultimos_7_dias": acessos_ultimos_7_dias,
            "acessos_qr": acessos_qr,
            "acessos_nfc": acessos_nfc,
        },
        "empresas": [
            {
                "id": empresa_id,
                "nome": nome,
                "total_qr_codes": total_qr_codes_empresa or 0,
                "qr_codes_ativos": qr_codes_ativos_empresa or 0,
                "total_acessos": total_acessos_empresa or 0,
                "acessos_ultimos_7_dias": acessos_ultimos_7_dias_empresa or 0,
                "acessos_qr": acessos_qr_empresa or 0,
                "acessos_nfc": acessos_nfc_empresa or 0,
                "ultimo_acesso": ultimo_acesso,
                "qr_codes": qr_codes_por_empresa.get(empresa_id, []),
            }
            for (
                empresa_id,
                nome,
                total_qr_codes_empresa,
                qr_codes_ativos_empresa,
                total_acessos_empresa,
                acessos_ultimos_7_dias_empresa,
                acessos_qr_empresa,
                acessos_nfc_empresa,
                ultimo_acesso,
            ) in empresas
        ],
        "acessos_recentes": [
            {
                "empresa": nome,
                "codigo": codigo,
                "origem": origem,
                "acessado_em": acessado_em,
            }
            for nome, codigo, origem, acessado_em in acessos_recentes
        ],
    }
