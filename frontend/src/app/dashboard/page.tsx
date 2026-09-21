"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";

import { supabase } from "@/lib/supabase";
import {
  formatReportTimestamp,
  readAccessSeries,
} from "@/lib/dashboard-report";

import ThemeToggle from "../components/ThemeToggle";
import AccessChart from "./AccessChart";
import WeeklyTrend from "./WeeklyTrend";
import styles from "./page.module.css";

type DashboardData = {
  empresa: {
    id: number;
    nome: string;
    stats_start_at?: string | null;
  };
  serie_temporal?: unknown;
  estatisticas: {
    acessos_hoje: number;
    acessos_ultimos_7_dias: number;
    total_acessos: number;
    acessos_qr: number;
    acessos_nfc: number;
    ultimo_acesso: string | null;
  };
  qr_atual: {
    codigo: string;
    destino_url: string;
    ativo: boolean;
  } | null;
  acessos_recentes: Array<{
    codigo: string;
    origem: "qr" | "nfc";
    acessado_em: string;
  }>;
};

type DashboardRole = "admin" | "client";

type EstablishmentOption = {
  id: number;
  nome: string;
};

type EstablishmentsResponse = {
  role: DashboardRole;
  empresas: EstablishmentOption[];
};

const apiUrl = process.env.NEXT_PUBLIC_API_URL;
const numberFormat = new Intl.NumberFormat("pt-BR");

function mensagemDeErro(error: unknown, fallback: string) {
  if (error instanceof TypeError)
    return "Não foi possível conectar ao servidor. Tente novamente.";
  return error instanceof Error ? error.message : fallback;
}

async function obterDetalheErro(resposta: Response) {
  let detalhe = `Erro HTTP ${resposta.status}`;

  try {
    const corpo = await resposta.json();

    if (typeof corpo.detail === "string") {
      detalhe = corpo.detail;
    }
  } catch {
    // Mantém o status HTTP quando a API não retorna JSON.
  }

  return detalhe;
}

export default function DashboardPage() {
  const router = useRouter();
  const [dados, setDados] = useState<DashboardData | null>(null);
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(true);
  const [role, setRole] = useState<DashboardRole | null>(null);
  const [establishments, setEstablishments] = useState<EstablishmentOption[]>(
    [],
  );
  const [selectedEstablishmentId, setSelectedEstablishmentId] = useState("");
  const [chartPeriod, setChartPeriod] = useState<7 | 30>(7);
  const [printGeneratedAt, setPrintGeneratedAt] = useState(() =>
    new Date().toISOString(),
  );
  const requestRef = useRef<AbortController | null>(null);
  const userIdRef = useRef<string | null>(null);

  const buscarOverview = useCallback(
    async (
      accessToken: string,
      establishmentId: number,
      signal: AbortSignal,
    ) => {
      const resposta = await fetch(
        `${apiUrl}/dashboard/overview?establishment_id=${establishmentId}`,
        {
          signal,
          cache: "no-store",
          headers: {
            Authorization: `Bearer ${accessToken}`,
          },
        },
      );

      if (signal.aborted) return null;

      if (resposta.status === 401) {
        await supabase.auth.signOut();
        router.replace("/");
        return null;
      }

      if (!resposta.ok) {
        throw new Error(await obterDetalheErro(resposta));
      }

      const overview = (await resposta.json()) as DashboardData;
      if (overview.empresa.id !== establishmentId) {
        throw new Error(
          "Os dados recebidos não correspondem à empresa selecionada.",
        );
      }
      return overview;
    },
    [router],
  );

  useEffect(() => {
    const controller = new AbortController();
    requestRef.current = controller;

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((event, session) => {
      const changedAccount =
        event === "SIGNED_IN" &&
        userIdRef.current !== null &&
        session?.user.id !== userIdRef.current;
      if (event === "SIGNED_OUT" || changedAccount) {
        requestRef.current?.abort();
        setDados(null);
        setRole(null);
        setEstablishments([]);
        setSelectedEstablishmentId("");
        setErro("");
        setCarregando(true);
        router.replace("/");
      }
    });

    async function carregarDashboard() {
      try {
        const {
          data: { session },
        } = await supabase.auth.getSession();

        if (controller.signal.aborted) return;

        if (!session) {
          router.replace("/");
          return;
        }
        userIdRef.current = session.user.id;

        if (!apiUrl) {
          throw new Error("A URL da API não foi configurada.");
        }

        const respostaEmpresas = await fetch(
          `${apiUrl}/dashboard/establishments`,
          {
            signal: controller.signal,
            cache: "no-store",
            headers: {
              Authorization: `Bearer ${session.access_token}`,
            },
          },
        );

        if (controller.signal.aborted) return;

        if (respostaEmpresas.status === 401) {
          await supabase.auth.signOut();
          router.replace("/");
          return;
        }

        if (!respostaEmpresas.ok) {
          throw new Error(await obterDetalheErro(respostaEmpresas));
        }

        const acesso =
          (await respostaEmpresas.json()) as EstablishmentsResponse;
        if (controller.signal.aborted) return;
        const primeiraEmpresa = acesso.empresas[0];

        if (!primeiraEmpresa) {
          throw new Error("Nenhuma empresa disponível para este usuário.");
        }

        setRole(acesso.role);
        setEstablishments(acesso.empresas);
        setSelectedEstablishmentId(String(primeiraEmpresa.id));
        setChartPeriod(7);

        const dadosDashboard = await buscarOverview(
          session.access_token,
          primeiraEmpresa.id,
          controller.signal,
        );

        if (controller.signal.aborted || !dadosDashboard) return;

        setDados(dadosDashboard);
        setErro("");
      } catch (error) {
        if (!controller.signal.aborted) {
          setErro(
            mensagemDeErro(error, "Não foi possível conectar ao servidor."),
          );
        }
      } finally {
        if (!controller.signal.aborted) setCarregando(false);
      }
    }

    carregarDashboard();

    return () => {
      requestRef.current?.abort();
      controller.abort();
      subscription.unsubscribe();
    };
  }, [buscarOverview, router]);

  async function carregarEmpresa(establishmentId: number) {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setSelectedEstablishmentId(String(establishmentId));
    setChartPeriod(7);
    setDados(null);
    setCarregando(true);
    setErro("");

    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();

      if (controller.signal.aborted) return;

      if (!session) {
        router.replace("/");
        return;
      }

      const dadosDashboard = await buscarOverview(
        session.access_token,
        establishmentId,
        controller.signal,
      );

      if (!controller.signal.aborted && dadosDashboard)
        setDados(dadosDashboard);
    } catch (error) {
      if (!controller.signal.aborted) {
        setErro(mensagemDeErro(error, "Não foi possível carregar a empresa."));
      }
    } finally {
      if (!controller.signal.aborted) setCarregando(false);
    }
  }

  async function sair() {
    requestRef.current?.abort();
    setDados(null);
    setCarregando(true);
    await supabase.auth.signOut();
    router.replace("/");
  }

  function imprimirRelatorio() {
    setPrintGeneratedAt(new Date().toISOString());
    window.setTimeout(() => window.print(), 0);
  }

  if (carregando && !role) {
    return (
      <main className={styles.state} role="status">
        Carregando seu painel...
      </main>
    );
  }

  if (!role) {
    return (
      <main className={styles.state}>
        <p>{erro || "Nenhum dado disponível."}</p>
        <button onClick={() => window.location.reload()}>
          Tentar novamente
        </button>
      </main>
    );
  }

  const estatisticas = dados?.estatisticas;
  const qrAtual = dados?.qr_atual;
  const companyName =
    establishments.find(
      (company) => String(company.id) === selectedEstablishmentId,
    )?.nome ?? "";
  const series =
    dados && estatisticas
      ? readAccessSeries(dados.serie_temporal, estatisticas)
      : null;
  const hasLargeCounts =
    estatisticas &&
    [
      estatisticas.acessos_hoje,
      estatisticas.acessos_ultimos_7_dias,
      estatisticas.total_acessos,
    ].some((count) => numberFormat.format(count).length > 6);

  return (
    <main className={styles.page}>
      <header className={`${styles.header} ${styles.noPrint}`}>
        <div className={styles.brand}>
          <Image
            className={styles.brandLogo}
            src="/brand/nl-light-green-transparent.png"
            alt="NL"
            width={490}
            height={212}
            priority
          />
          <span>NFC</span>
        </div>

        <div className={styles.headerActions}>
          <span className={styles.clientName}>{companyName}</span>
          <ThemeToggle />
          {role === "admin" && (
            <button
              className={styles.adminButton}
              onClick={() => router.push("/admin")}
            >
              Painel admin
            </button>
          )}
          <button className={styles.logoutButton} onClick={sair}>
            Sair
          </button>
        </div>
      </header>

      <section className={styles.content}>
        <div className={`${styles.heading} ${styles.noPrint}`}>
          <div>
            <p className={styles.eyebrow}>Visão geral</p>
            <h1>Olá, {companyName}.</h1>
            <p>Acompanhe como seus clientes estão chegando à avaliação.</p>
          </div>
          <div className={styles.headingActions}>
            {role === "admin" && (
              <label className={styles.companySelector}>
                <span>Visualizando empresa</span>
                <select
                  value={selectedEstablishmentId}
                  onChange={(event) =>
                    carregarEmpresa(Number(event.target.value))
                  }
                >
                  {establishments.map((establishment) => (
                    <option key={establishment.id} value={establishment.id}>
                      {establishment.nome}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {!carregando && !erro && dados && (
              <span className={styles.liveStatus}>● Dados atualizados</span>
            )}
            {!carregando && !erro && dados && (
              <button
                type="button"
                className={styles.printButton}
                onClick={imprimirRelatorio}
              >
                Imprimir ou salvar relatório
              </button>
            )}
          </div>
        </div>

        {carregando ? (
          <div className={styles.reportState} role="status">
            Carregando dados da empresa...
          </div>
        ) : erro || !dados || !estatisticas ? (
          <div className={styles.reportState} role="alert">
            <p>{erro || "Nenhum dado disponível."}</p>
            <button
              className={styles.logoutButton}
              onClick={() => carregarEmpresa(Number(selectedEstablishmentId))}
            >
              Tentar novamente
            </button>
          </div>
        ) : (
          <>
            <div className={styles.printHeader}>
              <p>Relatório de acessos</p>
              <h2>{companyName}</h2>
              <span>
                Gerado em {formatReportTimestamp(printGeneratedAt)} · gráfico
                diário: últimos {chartPeriod} dias · tendência semanal: últimos
                30 dias
              </span>
            </div>
            <section
              className={`${styles.metrics} ${hasLargeCounts ? styles.largeMetrics : ""}`}
              aria-label="Resumo de acessos"
            >
              <article className={styles.metricCard}>
                <span>Acessos hoje</span>
                <strong>
                  {numberFormat.format(estatisticas.acessos_hoje)}
                </strong>
                <small>Leituras registradas hoje</small>
              </article>
              <article className={styles.metricCard}>
                <span>Últimos 7 dias</span>
                <strong>
                  {numberFormat.format(estatisticas.acessos_ultimos_7_dias)}
                </strong>
                <small>Movimento recente</small>
              </article>
              <article className={`${styles.metricCard} ${styles.totalMetric}`}>
                <span>Total de acessos</span>
                <strong>
                  {numberFormat.format(estatisticas.total_acessos)}
                </strong>
                <small>
                  {dados.empresa.stats_start_at
                    ? `Desde ${formatReportTimestamp(dados.empresa.stats_start_at, true)}`
                    : "Desde o início das estatísticas"}
                </small>
              </article>
            </section>

            <AccessChart
              key={dados.empresa.id}
              series={series}
              period={chartPeriod}
              onPeriodChange={setChartPeriod}
            />

            <WeeklyTrend
              series={series}
              statsStartAt={dados.empresa.stats_start_at}
            />

            <section className={styles.mainGrid}>
              <article className={styles.panel}>
                <div className={styles.panelHeading}>
                  <div>
                    <p className={styles.eyebrow}>Atividade</p>
                    <h2>Acessos recentes</h2>
                  </div>
                  <span className={styles.totalLabel}>
                    {numberFormat.format(estatisticas.total_acessos)} no total
                  </span>
                </div>

                {dados.acessos_recentes.length === 0 ? (
                  <p className={styles.emptyState}>
                    Os acessos aparecerão aqui assim que alguém ler o QR Code ou
                    a tag NFC.
                  </p>
                ) : (
                  <div className={styles.accessList}>
                    {dados.acessos_recentes.map((acesso, index) => (
                      <div
                        className={styles.accessRow}
                        key={`${acesso.acessado_em}-${index}`}
                      >
                        <span className={styles.sourceIcon} aria-hidden="true">
                          <svg
                            width="18"
                            height="18"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="1.8"
                          >
                            <path d="M4 16l6-6 4 4 6-9M14 5h6v6" />
                          </svg>
                        </span>
                        <div>
                          <strong>Acesso registrado</strong>
                          <span>Código {acesso.codigo}</span>
                        </div>
                        <time dateTime={acesso.acessado_em}>
                          {formatReportTimestamp(acesso.acessado_em)}
                        </time>
                      </div>
                    ))}
                  </div>
                )}
              </article>

              <article className={styles.panel}>
                <div className={styles.panelHeading}>
                  <div>
                    <p className={styles.eyebrow}>Seu QR Code</p>
                    <h2>Destino atual</h2>
                  </div>
                  <span
                    className={
                      qrAtual?.ativo ? styles.activeTag : styles.inactiveTag
                    }
                  >
                    {qrAtual?.ativo ? "Ativo" : "Inativo"}
                  </span>
                </div>

                {qrAtual ? (
                  <div className={styles.destination}>
                    <span className={styles.qrCode}>{qrAtual.codigo}</span>
                    <p>{qrAtual.destino_url}</p>
                    <small>
                      O destino pode ser alterado sem gerar outro QR Code.
                    </small>
                  </div>
                ) : (
                  <p className={styles.emptyState}>
                    Nenhum QR Code cadastrado.
                  </p>
                )}

                <div className={styles.lastAccess}>
                  <span>Último acesso</span>
                  <strong>
                    {formatReportTimestamp(estatisticas.ultimo_acesso)}
                  </strong>
                </div>
              </article>
            </section>
          </>
        )}
      </section>
    </main>
  );
}
