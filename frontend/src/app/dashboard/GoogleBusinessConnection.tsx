"use client";

import { useEffect, useRef, useState } from "react";

import { supabase } from "@/lib/supabase";
import {
  createConnectionActionButton,
  disconnectAndReconcileStatus,
  getGoogleBusinessLocationLabel,
  getConnectionAction,
  reconcileConnectionStatusAfterConflict,
  type GoogleBusinessConnectionStatus,
  type GoogleBusinessLocationOption,
} from "@/lib/google-business-connection-state";

import styles from "./GoogleBusinessConnection.module.css";

type GoogleBusinessStatus = GoogleBusinessConnectionStatus;

type ApiError = Error & { status?: number };

const apiUrl = process.env.NEXT_PUBLIC_API_URL;

function erroDaApi(mensagem: string, status: number): ApiError {
  const erro = new Error(mensagem) as ApiError;
  erro.status = status;
  return erro;
}

async function lerDetalhe(resposta: Response) {
  try {
    const corpo = await resposta.json();
    if (typeof corpo.detail === "string") return corpo.detail;
  } catch {
    // Usa o status HTTP quando a API não retorna JSON.
  }
  return `Erro HTTP ${resposta.status}`;
}

async function requisitarApi<T>(
  caminho: string,
  init: RequestInit = {},
): Promise<T> {
  if (!apiUrl) throw new Error("A URL da API não foi configurada.");

  const {
    data: { session },
  } = await supabase.auth.getSession();

  if (!session) throw erroDaApi("Sessão expirada. Entre novamente.", 401);

  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${session.access_token}`);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const resposta = await fetch(`${apiUrl}${caminho}`, {
    ...init,
    headers,
    cache: "no-store",
  });

  if (!resposta.ok) {
    throw erroDaApi(await lerDetalhe(resposta), resposta.status);
  }

  return (await resposta.json()) as T;
}

export default function GoogleBusinessConnection() {
  const [status, setStatus] = useState<GoogleBusinessStatus | null>(null);
  const [locais, setLocais] = useState<GoogleBusinessLocationOption[]>([]);
  const [localSelecionado, setLocalSelecionado] = useState("");
  const [carregando, setCarregando] = useState(true);
  const [processando, setProcessando] = useState(false);
  const [mensagem, setMensagem] = useState("");
  const [erro, setErro] = useState("");
  const cargaInicialRef = useRef<Promise<{
    status: GoogleBusinessStatus | null;
    locais: GoogleBusinessLocationOption[];
    mensagem: string;
    erro: string;
  }> | null>(null);

  useEffect(() => {
    let ativo = true;
    if (!cargaInicialRef.current) {
      cargaInicialRef.current = (async () => {
        const parametros = new URLSearchParams(window.location.search);
        const fragmento = new URLSearchParams(window.location.hash.slice(1));
        const resultadoOAuth =
          fragmento.get("google_business") ??
          parametros.get("google_business");
        const stateOAuth =
          fragmento.get("google_business_state") ??
          parametros.get("google_business_state");
        let mensagemInicial = "";
        let erroInicial = "";

        if (resultadoOAuth) {
          const url = new URL(window.location.href);
          url.searchParams.delete("google_business");
          url.searchParams.delete("google_business_state");
          if (fragmento.has("google_business")) url.hash = "";
          window.history.replaceState(
            {},
            "",
            `${url.pathname}${url.search}${url.hash}`,
          );
        }

        if (resultadoOAuth === "complete") {
          if (!stateOAuth) {
            erroInicial = "O retorno Google não contém um estado válido. Reinicie a conexão.";
          } else {
            try {
              await requisitarApi<{ conectado: boolean }>(
                "/dashboard/google-business/callback/complete",
                {
                  method: "POST",
                  body: JSON.stringify({ state: stateOAuth }),
                },
              );
              mensagemInicial = "Conta Google conectada. Escolha o local da empresa.";
            } catch (error) {
              const apiError = error as ApiError;
              erroInicial =
                apiError.message ||
                "Não foi possível concluir a conexão Google. Reinicie o fluxo.";
            }
          }
        } else if (resultadoOAuth === "authorization_denied") {
          erroInicial = "A autorização Google foi cancelada.";
        } else if (resultadoOAuth === "exchange_failed") {
          erroInicial = "O Google não concluiu a autorização. Reinicie a conexão.";
        } else if (
          resultadoOAuth &&
          resultadoOAuth !== "connected"
        ) {
          erroInicial = "Não foi possível concluir a conexão Google.";
        } else if (resultadoOAuth === "connected") {
          mensagemInicial = "Conta Google conectada. Escolha o local da empresa.";
        }

        let statusInicial: GoogleBusinessStatus | null = null;
        let locaisIniciais: GoogleBusinessLocationOption[] = [];
        try {
          statusInicial = await requisitarApi<GoogleBusinessStatus>(
            "/dashboard/google-business/status",
          );
          if (statusInicial.conectado) {
            const resposta = await requisitarApi<{
              locais: GoogleBusinessLocationOption[];
            }>("/dashboard/google-business/locations");
            locaisIniciais = resposta.locais;
          }
        } catch (error) {
          const apiError = error as ApiError;
          statusInicial = await reconcileConnectionStatusAfterConflict(
            statusInicial,
            apiError.status,
            () =>
              requisitarApi<GoogleBusinessStatus>(
                "/dashboard/google-business/status",
              ),
          );
          if (statusInicial?.conectado !== true) locaisIniciais = [];
          erroInicial ||=
            apiError.message || "Não foi possível ler a conexão Google.";
        }

        return {
          status: statusInicial,
          locais: locaisIniciais,
          mensagem: mensagemInicial,
          erro: erroInicial,
        };
      })();
    }

    cargaInicialRef.current.then((inicial) => {
      if (!ativo) return;
      setStatus(inicial.status);
      setLocalSelecionado(inicial.status?.local?.nome ?? "");
      setLocais(inicial.locais);
      setMensagem(inicial.mensagem);
      setErro(inicial.erro);
      setCarregando(false);
    });

    return () => {
      ativo = false;
    };
  }, []);

  async function conectar() {
    setProcessando(true);
    setErro("");
    setMensagem("");
    try {
      const resposta = await requisitarApi<{ authorization_url: string }>(
        "/dashboard/google-business/connect",
      );
      window.location.assign(resposta.authorization_url);
    } catch (error) {
      const apiError = error as ApiError;
      setErro(apiError.message || "Não foi possível iniciar a conexão Google.");
      setProcessando(false);
    }
  }

  async function verificarConexao() {
    setProcessando(true);
    setErro("");
    try {
      const statusAtualizado = await requisitarApi<GoogleBusinessStatus>(
        "/dashboard/google-business/status",
      );
      let locaisAtualizados: GoogleBusinessLocationOption[] = [];
      if (statusAtualizado.conectado) {
        const resposta = await requisitarApi<{
          locais: GoogleBusinessLocationOption[];
        }>(
          "/dashboard/google-business/locations",
        );
        locaisAtualizados = resposta.locais;
      }
      setStatus(statusAtualizado);
      setLocais(locaisAtualizados);
      setLocalSelecionado(statusAtualizado.local?.nome ?? "");
    } catch (error) {
      const apiError = error as ApiError;
      const statusAtualizado = await reconcileConnectionStatusAfterConflict(
        status,
        apiError.status,
        () =>
          requisitarApi<GoogleBusinessStatus>(
            "/dashboard/google-business/status",
          ),
      );
      setStatus(statusAtualizado);
      if (statusAtualizado?.conectado !== true) {
        setLocais([]);
        setLocalSelecionado("");
      }
      setErro(apiError.message || "Não foi possível verificar a conexão Google.");
    } finally {
      setProcessando(false);
    }
  }

  async function selecionarLocal() {
    if (!localSelecionado) return;
    setProcessando(true);
    setErro("");
    setMensagem("");
    try {
      const resposta = await requisitarApi<{
        local: { nome: string; titulo: string };
      }>("/dashboard/google-business/location", {
        method: "POST",
        body: JSON.stringify({ nome: localSelecionado }),
      });
      setStatus((atual) =>
        atual
          ? {
              ...atual,
              conectado: true,
              status: "connected",
              local: resposta.local,
            }
          : atual,
      );
      setMensagem("Local vinculado à empresa.");
    } catch (error) {
      const apiError = error as ApiError;
      setErro(apiError.message || "Não foi possível vincular o local.");
      const statusAtualizado = await reconcileConnectionStatusAfterConflict(
        status,
        apiError.status,
        () =>
          requisitarApi<GoogleBusinessStatus>(
            "/dashboard/google-business/status",
          ),
      );
      if (statusAtualizado !== status) {
        setStatus(statusAtualizado);
        if (statusAtualizado?.conectado !== true) {
          setLocais([]);
          setLocalSelecionado("");
        }
      }
    } finally {
      setProcessando(false);
    }
  }

  async function desconectar() {
    setProcessando(true);
    setErro("");
    setMensagem("");
    try {
      const resultado = await disconnectAndReconcileStatus(
        () =>
          requisitarApi<{ conectado: false }>(
            "/dashboard/google-business",
            { method: "DELETE" },
          ),
        () =>
          requisitarApi<GoogleBusinessStatus>(
            "/dashboard/google-business/status",
          ),
      );
      setStatus(resultado.status);

      if (resultado.status?.conectado !== true) {
        setLocais([]);
        setLocalSelecionado("");
      }

      if (resultado.kind === "disconnected") {
        setMensagem("Conta Google desvinculada desta empresa.");
      } else {
        const apiError = resultado.error as ApiError;
        setErro(
          apiError.message || "Não foi possível desvincular a conta desta empresa.",
        );
      }
    } finally {
      setProcessando(false);
    }
  }

  if (carregando) {
    return (
      <section className={styles.panel} aria-label="Google Business Profile">
        <p className={styles.muted}>Verificando conexão Google...</p>
      </section>
    );
  }

  const conectado = status?.conectado === true;
  const acaoConexao = getConnectionAction(status);

  return (
    <section className={styles.panel} aria-labelledby="google-business-title">
      <div className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>Integração</p>
          <h2 id="google-business-title">Google Business Profile</h2>
        </div>
        <span className={conectado ? styles.connected : styles.disconnected}>
          {status?.status === "unknown"
            ? "Status indisponível"
            : conectado
              ? "Conectado"
              : "Não conectado"}
        </span>
      </div>

      <p className={styles.description}>
        Conecte a conta que administra a ficha da empresa para vincular um local.
      </p>

      {erro && (
        <p className={styles.error} role="alert">
          {erro}
        </p>
      )}
      {mensagem && <p className={styles.message}>{mensagem}</p>}

      {acaoConexao ? (
        createConnectionActionButton({
          action: acaoConexao,
          isProcessing: processando,
          onClick: acaoConexao === "retry" ? verificarConexao : conectar,
          disabled: processando,
          className: styles.primaryButton,
        })
      ) : status ? (
        <div className={styles.controls}>
          {status.local && (
            <p className={styles.currentLocation}>
              Local atual: <strong>{status.local.titulo}</strong>
            </p>
          )}

          {locais.length > 0 ? (
            <label className={styles.locationLabel}>
              <span>Local vinculado à empresa</span>
              <select
                value={localSelecionado}
                onChange={(event) => setLocalSelecionado(event.target.value)}
                disabled={processando}
              >
                <option value="">Escolha um local</option>
                {locais.map((local) => (
                  <option key={local.nome} value={local.nome}>
                    {getGoogleBusinessLocationLabel(local, locais)}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <p className={styles.muted}>
              Nenhum local administrável foi encontrado nessa conta Google.
            </p>
          )}

          <p className={styles.muted}>
            Esta ação desvincula somente esta empresa do NL Reviews. Ela não revoga
            o acesso do aplicativo à sua Conta Google.
          </p>

          <div className={styles.actions}>
            <button
              type="button"
              className={styles.primaryButton}
              onClick={selecionarLocal}
              disabled={processando || !localSelecionado}
            >
              {processando ? "Salvando..." : "Salvar local"}
            </button>
            <button
              type="button"
              className={styles.secondaryButton}
              onClick={desconectar}
              disabled={processando}
            >
              Desvincular esta empresa
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
