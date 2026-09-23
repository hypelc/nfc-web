from fastapi import FastAPI, HTTPException  # Biblioteca para criar a API e tratar exceções HTTP
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse # Biblioteca para redirecionar o usuário para outra URL

from app.database import abrir_conexao # Função para abrir a conexão com o banco de dados
from app.config import CORS_ORIGINS
from app.routers.dashboard import router as dashboard_router

app = FastAPI() # Cria o objeto principal da API. O Uvicorn procura esse objeto quando executamos - python -m uvicorn app.main:app --reload

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=r"^https://(?:nfc-web-git-[a-z0-9-]+|nfc-[a-z0-9]+)-jeanluccasgl-3636s-projects\.vercel\.app$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(dashboard_router, prefix="/dashboard", tags=["dashboard"])

@app.get("/") # Informa ao FastAPI que a função abaixo será executada quando alguém acessar a rota raiz ("/") da API usando o método GET.
def inicio():
    return {"mensagem": "API funcionando corretamente."}

@app.get("/q/{codigo}") # Informa ao FastAPI que a função abaixo será executada quando alguém acessar a rota "/q/{codigo}" da API usando o método GET. O {codigo} é um parâmetro de caminho que será passado para a função acessar_qr_code.
def acessar_qr_code(codigo: str, source: str = "qr"): # A função recebe o parâmetro codigo, que é uma string representando o código do QR Code que o usuário deseja acessar.

    if source not in ("qr", "nfc"):
        raise HTTPException(
            status_code=400,
            detail="Origem inválida",
    )

    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            cursor.execute(
                """
                SELECT qr_codes.id, qr_codes.destination_url
                FROM qr_codes
                JOIN establishments ON establishments.id = qr_codes.establishment_id
                WHERE qr_codes.code = %s
                  AND qr_codes.is_active = TRUE
                  AND establishments.archived_at IS NULL
                """,
                (codigo,),
            )

            qr_code = cursor.fetchone()

            if qr_code is None:
                raise HTTPException(
                    status_code=404,
                    detail="QR Code não encontrado",
                )

            qr_code_id, destination_url = qr_code

            cursor.execute(
                """
                INSERT INTO access_events (qr_code_id, source)
                VALUES (%s, %s)
                """,
                (qr_code_id, source),
            )

    return RedirectResponse(
        url=destination_url,
        status_code=302,
    )
