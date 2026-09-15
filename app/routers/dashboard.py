import io
import secrets
from urllib.parse import urlparse

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.auth import obter_usuario_atual
from app.config import PUBLIC_BASE_URL
from app.database import abrir_conexao


router = APIRouter()


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
                RETURNING code, destination_url, is_active
                """,
                (dados.link_avaliacao, codigo),
            )
            qr_code = cursor.fetchone()

    if qr_code is None:
        raise HTTPException(status_code=404, detail="QR Code não encontrado")

    return {
        "codigo": qr_code[0],
        "destino_url": qr_code[1],
        "ativo": qr_code[2],
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
                RETURNING code, destination_url, is_active
                """,
                (dados.ativo, codigo),
            )
            qr_code = cursor.fetchone()

    if qr_code is None:
        raise HTTPException(status_code=404, detail="QR Code não encontrado")

    return {
        "codigo": qr_code[0],
        "destino_url": qr_code[1],
        "ativo": qr_code[2],
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


@router.delete("/admin/establishments/{establishment_id}")
def excluir_empresa(
    establishment_id: int = Path(gt=0),
    usuario: dict = Depends(obter_usuario_atual),
):
    """Exclui uma empresa, seus QR Codes e o histórico de acessos."""

    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            _exigir_admin(cursor, usuario["id"])
            cursor.execute(
                """
                DELETE FROM access_events
                WHERE qr_code_id IN (
                    SELECT id FROM qr_codes WHERE establishment_id = %s
                )
                """,
                (establishment_id,),
            )
            cursor.execute(
                """
                DELETE FROM qr_codes
                WHERE establishment_id = %s
                """,
                (establishment_id,),
            )
            cursor.execute(
                """
                DELETE FROM establishments
                WHERE id = %s
                RETURNING id, name
                """,
                (establishment_id,),
            )
            empresa = cursor.fetchone()

    if empresa is None:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    return {"id": empresa[0], "nome": empresa[1]}


@router.post("/admin/establishments/{establishment_id}/reset-accesses")
def resetar_acessos_empresa(
    establishment_id: int = Path(gt=0),
    usuario: dict = Depends(obter_usuario_atual),
):
    """Remove o histórico de leituras de uma empresa sem apagar seu cadastro."""

    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            _exigir_admin(cursor, usuario["id"])

            cursor.execute(
                """
                SELECT id, name
                FROM establishments
                WHERE id = %s
                """,
                (establishment_id,),
            )
            empresa = cursor.fetchone()

            if empresa is None:
                raise HTTPException(status_code=404, detail="Empresa não encontrada")

            cursor.execute(
                """
                DELETE FROM access_events
                WHERE qr_code_id IN (
                    SELECT id
                    FROM qr_codes
                    WHERE establishment_id = %s
                )
                RETURNING id
                """,
                (establishment_id,),
            )
            acessos_excluidos = len(cursor.fetchall())

    return {
        "id": empresa[0],
        "nome": empresa[1],
        "acessos_excluidos": acessos_excluidos,
    }


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
                    ORDER BY name
                    """
                )
            else:
                cursor.execute(
                    """
                    SELECT id, name
                    FROM establishments
                    WHERE id = %s
                    """,
                    (client_establishment_id,),
                )

            empresas = cursor.fetchall()

    return {
        "role": role,
        "empresas": [
            {"id": empresa_id, "nome": nome}
            for empresa_id, nome in empresas
        ],
    }


@router.get("/overview")
def obter_resumo_dashboard(
    establishment_id: int | None = Query(default=None, gt=0),
    usuario: dict = Depends(obter_usuario_atual),
):
    """Retorna o resumo de uma empresa permitida ao usuário."""

    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
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
                SELECT id, name
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

            establishment_id, establishment_name = establishment

            cursor.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE ae.accessed_at >= CURRENT_DATE
                    ) AS acessos_hoje,
                    COUNT(*) FILTER (
                        WHERE ae.accessed_at >= CURRENT_DATE - INTERVAL '6 days'
                    ) AS acessos_ultimos_7_dias,
                    COUNT(ae.id) AS total_acessos,
                    COUNT(*) FILTER (
                        WHERE ae.source = 'qr'
                    ) AS acessos_qr,
                    COUNT(*) FILTER (
                        WHERE ae.source = 'nfc'
                    ) AS acessos_nfc,
                    MAX(ae.accessed_at) AS ultimo_acesso
                FROM qr_codes qc
                LEFT JOIN access_events ae ON ae.qr_code_id = qc.id
                WHERE qc.establishment_id = %s
                """,
                (establishment_id,),
            )
            estatisticas = cursor.fetchone()

            cursor.execute(
                """
                SELECT code, destination_url, is_active
                FROM qr_codes
                WHERE establishment_id = %s
                ORDER BY created_at DESC, id DESC
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
                WHERE qc.establishment_id = %s
                ORDER BY ae.accessed_at DESC
                LIMIT 10
                """,
                (establishment_id,),
            )
            acessos_recentes = cursor.fetchall()

    (
        acessos_hoje,
        acessos_ultimos_7_dias,
        total_acessos,
        acessos_qr,
        acessos_nfc,
        ultimo_acesso,
    ) = estatisticas

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
        },
        "estatisticas": {
            "acessos_hoje": acessos_hoje,
            "acessos_ultimos_7_dias": acessos_ultimos_7_dias,
            "total_acessos": total_acessos,
            "acessos_qr": acessos_qr,
            "acessos_nfc": acessos_nfc,
            "ultimo_acesso": ultimo_acesso,
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

            cursor.execute(
                """
                SELECT
                    e.id,
                    e.name,
                    COUNT(DISTINCT qc.id) AS total_qr_codes,
                    COUNT(DISTINCT qc.id) FILTER (
                        WHERE qc.is_active = TRUE
                    ) AS qr_codes_ativos,
                    COUNT(ae.id) AS total_acessos,
                    COUNT(ae.id) FILTER (
                        WHERE ae.accessed_at >= CURRENT_DATE - INTERVAL '6 days'
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
                LEFT JOIN access_events ae ON ae.qr_code_id = qc.id
                GROUP BY e.id, e.name
                ORDER BY e.name
                """
            )
            empresas = cursor.fetchall()

            cursor.execute(
                """
                SELECT e.name, qc.code, ae.source, ae.accessed_at
                FROM access_events ae
                JOIN qr_codes qc ON qc.id = ae.qr_code_id
                JOIN establishments e ON e.id = qc.establishment_id
                ORDER BY ae.accessed_at DESC
                LIMIT 12
            """
            )
            acessos_recentes = cursor.fetchall()

            cursor.execute(
                """
                SELECT id, establishment_id, code, destination_url, is_active
                FROM qr_codes
                ORDER BY created_at DESC, id DESC
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
