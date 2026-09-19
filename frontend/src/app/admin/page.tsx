"use client";

import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { supabase } from "@/lib/supabase";

import ThemeToggle from "../components/ThemeToggle";
import styles from "./page.module.css";

type AdminOverview = {
  indicadores: {
    total_empresas: number;
    total_qr_codes: number;
    qr_codes_ativos: number;
    total_acessos: number;
    acessos_ultimos_7_dias: number;
    acessos_qr: number;
    acessos_nfc: number;
  };
  empresas: Array<{
    id: number;
    nome: string;
    total_qr_codes: number;
    qr_codes_ativos: number;
    total_acessos: number;
    acessos_ultimos_7_dias: number;
    acessos_qr: number;
    acessos_nfc: number;
    ultimo_acesso: string | null;
    qr_codes: Array<{
      id: number;
      codigo: string;
      destino_url: string;
      ativo: boolean;
      url_publica: string;
    }>;
  }>;
  acessos_recentes: Array<{
    empresa: string;
    codigo: string;
    origem: "qr" | "nfc";
    acessado_em: string;
  }>;
  empresas_arquivadas: Array<{
    id: number;
    nome: string;
    arquivada_em: string;
    total_qr_codes: number;
  }>;
};

type CadastroEmpresaResponse = {
  empresa: {
    id: number;
    nome: string;
  };
  qr_code: {
    id: number;
    codigo: string;
    url_publica: string;
    destino_url: string;
  };
};

type QrCodeAdmin = AdminOverview["empresas"][number]["qr_codes"][number];

const apiUrl = process.env.NEXT_PUBLIC_API_URL;

function formatarData(data: string | null) {
  if (!data) return "Nunca";

  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(data));
}

async function obterDetalheErro(resposta: Response) {
  try {
    const corpo = await resposta.json();

    if (typeof corpo.detail === "string") return corpo.detail;
  } catch {
    // Usa uma mensagem genérica quando a API não retorna JSON.
  }

  return `Erro HTTP ${resposta.status}`;
}

export default function AdminPage() {
  const router = useRouter();
  const [dados, setDados] = useState<AdminOverview | null>(null);
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(true);
  const [nomeEmpresa, setNomeEmpresa] = useState("");
  const [linkAvaliacao, setLinkAvaliacao] = useState("");
  const [mensagemAcao, setMensagemAcao] = useState("");
  const [resultadoCadastro, setResultadoCadastro] =
    useState<CadastroEmpresaResponse | null>(null);
  const [salvandoEmpresa, setSalvandoEmpresa] = useState(false);
  const [empresaEditandoId, setEmpresaEditandoId] = useState<number | null>(null);
  const [nomeEditado, setNomeEditado] = useState("");
  const [qrEditandoCodigo, setQrEditandoCodigo] = useState<string | null>(null);
  const [destinoEditado, setDestinoEditado] = useState("");
  const [qrSelecionado, setQrSelecionado] = useState<QrCodeAdmin | null>(null);
  const [qrImagemUrl, setQrImagemUrl] = useState("");
  const [mensagemGerenciamento, setMensagemGerenciamento] = useState("");
  const [processandoAcao, setProcessandoAcao] = useState(false);

  const requisicaoAdmin = useCallback(
    async (caminho: string, init: RequestInit = {}) => {
      const {
        data: { session },
      } = await supabase.auth.getSession();

      if (!session) {
        router.replace("/");
        return null;
      }

      if (!apiUrl) {
        throw new Error("A URL da API não foi configurada.");
      }

      const resposta = await fetch(`${apiUrl}${caminho}`, {
        ...init,
        headers: {
          Authorization: `Bearer ${session.access_token}`,
          ...(init.body ? { "Content-Type": "application/json" } : {}),
          ...init.headers,
        },
      });

      if (resposta.status === 401) {
        await supabase.auth.signOut();
        router.replace("/");
        return null;
      }

      if (!resposta.ok) {
        throw new Error(await obterDetalheErro(resposta));
      }

      return resposta;
    },
    [router],
  );

  const carregarPainelAdmin = useCallback(async () => {
    setCarregando(true);

    try {
      const [resposta, respostaArquivadas] = await Promise.all([
        requisicaoAdmin("/dashboard/admin/overview"),
        requisicaoAdmin("/dashboard/admin/archived-establishments"),
      ]);

      if (!resposta || !respostaArquivadas) return;

      const resumo = (await resposta.json()) as Omit<
        AdminOverview,
        "empresas_arquivadas"
      >;
      const arquivadas = (await respostaArquivadas.json()) as {
        empresas: AdminOverview["empresas_arquivadas"];
      };

      setDados({ ...resumo, empresas_arquivadas: arquivadas.empresas });
      setErro("");
    } catch (error) {
      setErro(
        error instanceof Error
          ? error.message
          : "Não foi possível carregar o painel administrativo.",
      );
    } finally {
      setCarregando(false);
    }
  }, [requisicaoAdmin]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      carregarPainelAdmin();
    }, 0);

    return () => window.clearTimeout(timeoutId);
  }, [carregarPainelAdmin]);

  async function sair() {
    await supabase.auth.signOut();
    router.replace("/");
  }

  async function cadastrarEmpresa(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMensagemAcao("");
    setResultadoCadastro(null);
    setSalvandoEmpresa(true);

    try {
      const resposta = await requisicaoAdmin("/dashboard/admin/establishments", {
        method: "POST",
        body: JSON.stringify({
          nome: nomeEmpresa,
          link_avaliacao: linkAvaliacao,
        }),
      });

      if (!resposta) return;

      const resultado = (await resposta.json()) as CadastroEmpresaResponse;
      setResultadoCadastro(resultado);
      setMensagemAcao("Empresa cadastrada com sucesso.");
      setNomeEmpresa("");
      setLinkAvaliacao("");
      await carregarPainelAdmin();
    } catch (error) {
      setMensagemAcao(
        error instanceof Error ? error.message : "Não foi possível cadastrar a empresa.",
      );
    } finally {
      setSalvandoEmpresa(false);
    }
  }

  async function salvarNomeEmpresa(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (empresaEditandoId === null) return;

    setProcessandoAcao(true);
    setMensagemGerenciamento("");

    try {
      const resposta = await requisicaoAdmin(
        `/dashboard/admin/establishments/${empresaEditandoId}`,
        {
          method: "PATCH",
          body: JSON.stringify({ nome: nomeEditado }),
        },
      );

      if (!resposta) return;

      setEmpresaEditandoId(null);
      setMensagemGerenciamento("Nome da empresa atualizado.");
      await carregarPainelAdmin();
    } catch (error) {
      setMensagemGerenciamento(
        error instanceof Error ? error.message : "Não foi possível atualizar a empresa.",
      );
    } finally {
      setProcessandoAcao(false);
    }
  }

  async function salvarDestinoQr(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!qrEditandoCodigo) return;

    setProcessandoAcao(true);
    setMensagemGerenciamento("");

    try {
      const resposta = await requisicaoAdmin(
        `/dashboard/admin/qr-codes/${encodeURIComponent(qrEditandoCodigo)}/destination`,
        {
          method: "PATCH",
          body: JSON.stringify({ link_avaliacao: destinoEditado }),
        },
      );

      if (!resposta) return;

      setQrEditandoCodigo(null);
      setMensagemGerenciamento("Destino do QR Code atualizado.");
      await carregarPainelAdmin();
    } catch (error) {
      setMensagemGerenciamento(
        error instanceof Error ? error.message : "Não foi possível atualizar o destino.",
      );
    } finally {
      setProcessandoAcao(false);
    }
  }

  async function alternarQr(qrCode: QrCodeAdmin) {
    setProcessandoAcao(true);
    setMensagemGerenciamento("");

    try {
      const resposta = await requisicaoAdmin(
        `/dashboard/admin/qr-codes/${encodeURIComponent(qrCode.codigo)}/status`,
        {
          method: "PATCH",
          body: JSON.stringify({ ativo: !qrCode.ativo }),
        },
      );

      if (!resposta) return;

      setMensagemGerenciamento(
        qrCode.ativo ? "QR Code desativado." : "QR Code ativado.",
      );
      await carregarPainelAdmin();
    } catch (error) {
      setMensagemGerenciamento(
        error instanceof Error ? error.message : "Não foi possível alterar o status.",
      );
    } finally {
      setProcessandoAcao(false);
    }
  }

  async function arquivarEmpresa(empresaId: number, nome: string) {
    const nomeConfirmado = window.prompt(
      `Arquivar ${nome}? Os QR Codes e a tag NFC deixarão de redirecionar até a restauração.\n\nDigite o nome da empresa para confirmar:`,
    );

    if (nomeConfirmado?.trim() !== nome) return;

    setProcessandoAcao(true);
    setMensagemGerenciamento("");

    try {
      const resposta = await requisicaoAdmin(
        `/dashboard/admin/establishments/${empresaId}/archive`,
        { method: "POST" },
      );

      if (!resposta) return;

      setMensagemGerenciamento(
        `${nome} foi arquivada. Seus QR Codes e NFC estão pausados até a restauração.`,
      );
      setQrSelecionado(null);
      setQrImagemUrl("");
      await carregarPainelAdmin();
    } catch (error) {
      setMensagemGerenciamento(
        error instanceof Error ? error.message : "Não foi possível arquivar a empresa.",
      );
    } finally {
      setProcessandoAcao(false);
    }
  }

  async function restaurarEmpresa(empresaId: number, nome: string) {
    const confirmado = window.confirm(
      `Restaurar ${nome}? Os QR Codes e a tag NFC voltarão a respeitar seus estados individuais.`,
    );

    if (!confirmado) return;

    setProcessandoAcao(true);
    setMensagemGerenciamento("");

    try {
      const resposta = await requisicaoAdmin(
        `/dashboard/admin/establishments/${empresaId}/restore`,
        { method: "POST" },
      );

      if (!resposta) return;

      setMensagemGerenciamento(`${nome} foi restaurada com sucesso.`);
      await carregarPainelAdmin();
    } catch (error) {
      setMensagemGerenciamento(
        error instanceof Error ? error.message : "Não foi possível restaurar a empresa.",
      );
    } finally {
      setProcessandoAcao(false);
    }
  }

  async function iniciarNovoPeriodo(empresaId: number, nome: string) {
    const confirmado = window.confirm(
      `Iniciar um novo período para ${nome}? Os acessos antigos serão preservados, mas os cartões passarão a contar a partir de agora.`,
    );

    if (!confirmado) return;

    setProcessandoAcao(true);
    setMensagemGerenciamento("");

    try {
      const resposta = await requisicaoAdmin(
        `/dashboard/admin/establishments/${empresaId}/start-stats-period`,
        { method: "POST" },
      );

      if (!resposta) return;

      setMensagemGerenciamento(
        `Novo período iniciado para ${nome}. O histórico anterior foi preservado.`,
      );
      await carregarPainelAdmin();
    } catch (error) {
      setMensagemGerenciamento(
        error instanceof Error
          ? error.message
          : "Não foi possível iniciar um novo período.",
      );
    } finally {
      setProcessandoAcao(false);
    }
  }

  async function mostrarQr(qrCode: QrCodeAdmin) {
    setProcessandoAcao(true);
    setMensagemGerenciamento("");

    try {
      const resposta = await requisicaoAdmin(
        `/dashboard/admin/qr-codes/${encodeURIComponent(qrCode.codigo)}/image`,
      );

      if (!resposta) return;

      const imagem = await resposta.blob();
      const urlAnterior = qrImagemUrl;

      if (urlAnterior) URL.revokeObjectURL(urlAnterior);

      setQrSelecionado(qrCode);
      setQrImagemUrl(URL.createObjectURL(imagem));
    } catch (error) {
      setMensagemGerenciamento(
        error instanceof Error ? error.message : "Não foi possível carregar o QR Code.",
      );
    } finally {
      setProcessandoAcao(false);
    }
  }

  if (carregando) {
    return <main className={styles.state}>Carregando painel administrativo...</main>;
  }

  if (erro || !dados) {
    return (
      <main className={styles.state}>
        <p>{erro || "Nenhum dado disponível."}</p>
        <div className={styles.stateActions}>
          <button onClick={() => window.location.reload()}>Tentar novamente</button>
          <Link href="/dashboard">Voltar ao painel</Link>
        </div>
      </main>
    );
  }

  const { indicadores } = dados;

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div className={styles.brand}>
          <Image
            className={styles.brandLogo}
            src="/brand/nl-light-green-transparent.png"
            alt="NL"
            width={490}
            height={212}
            priority
          />
          <span className={styles.adminBadge}>Administração</span>
        </div>

        <div className={styles.headerActions}>
          <ThemeToggle />
          <Link className={styles.secondaryButton} href="/dashboard">
            Painel de empresa
          </Link>
          <button className={styles.logoutButton} onClick={sair}>
            Sair
          </button>
        </div>
      </header>

      <section className={styles.content}>
        <div className={styles.heading}>
          <div>
            <p className={styles.eyebrow}>Visão geral da operação</p>
            <h1>Painel administrativo</h1>
            <p>
              Acompanhe empresas, QR Codes, tags NFC e o movimento geral do sistema.
            </p>
          </div>
          <span className={styles.liveStatus}>● Dados atualizados</span>
        </div>

        <section className={styles.metrics} aria-label="Resumo da operação">
          <article className={styles.metricCard}>
            <span>Empresas ativas</span>
            <strong>{indicadores.total_empresas}</strong>
            <small>Estabelecimentos disponíveis</small>
          </article>
          <article className={styles.metricCard}>
            <span>Acessos totais</span>
            <strong>{indicadores.total_acessos}</strong>
            <small>Leituras registradas</small>
          </article>
          <article className={styles.metricCard}>
            <span>Últimos 7 dias</span>
            <strong>{indicadores.acessos_ultimos_7_dias}</strong>
            <small>Movimento recente</small>
          </article>
          <article className={styles.metricCard}>
            <span>QR / NFC</span>
            <strong>
              {indicadores.acessos_qr} <em>/</em> {indicadores.acessos_nfc}
            </strong>
            <small>{indicadores.qr_codes_ativos} QR Codes ativos</small>
          </article>
        </section>

        <section className={styles.actionGrid}>
          <article className={styles.panel}>
            <div className={styles.panelHeading}>
              <div>
                <p className={styles.eyebrow}>Nova operação</p>
                <h2>Cadastrar empresa</h2>
              </div>
            </div>

            <form className={styles.actionForm} onSubmit={cadastrarEmpresa}>
              <label>
                Nome da empresa
                <input
                  type="text"
                  value={nomeEmpresa}
                  onChange={(event) => setNomeEmpresa(event.target.value)}
                  placeholder="Ex.: Café Central"
                  minLength={2}
                  maxLength={120}
                  required
                />
              </label>
              <label>
                Link de avaliação
                <input
                  type="url"
                  value={linkAvaliacao}
                  onChange={(event) => setLinkAvaliacao(event.target.value)}
                  placeholder="https://g.page/..."
                  maxLength={2048}
                  required
                />
              </label>
              <button type="submit" disabled={salvandoEmpresa}>
                {salvandoEmpresa ? "Cadastrando..." : "Cadastrar e gerar QR"}
              </button>
              {mensagemAcao && (
                <p className={styles.actionMessage} role="status">
                  {mensagemAcao}
                </p>
              )}
            </form>
          </article>

          <article className={styles.panel}>
            <div className={styles.panelHeading}>
              <div>
                <p className={styles.eyebrow}>Resultado</p>
                <h2>Próximo passo</h2>
              </div>
            </div>
            {resultadoCadastro ? (
              <div className={styles.resultCard}>
                <strong>{resultadoCadastro.empresa.nome}</strong>
                <span>Código: {resultadoCadastro.qr_code.codigo}</span>
                <span>URL pública: {resultadoCadastro.qr_code.url_publica}</span>
                <small>
                  Use essa URL no QR Code e na tag NFC. O destino atual é o link de avaliação informado.
                </small>
              </div>
            ) : (
              <p className={styles.emptyState}>
                Depois do cadastro, o código e a URL pública aparecerão aqui.
              </p>
            )}
          </article>
        </section>

        <section className={styles.mainGrid}>
          <article className={styles.panel}>
            <div className={styles.panelHeading}>
              <div>
                <p className={styles.eyebrow}>Carteira</p>
                <h2>Empresas ativas</h2>
              </div>
              <span className={styles.totalLabel}>{dados.empresas.length} empresas</span>
            </div>

            <div className={styles.companyManagementGrid}>
              <div className={styles.tableWrapper}>
                <table className={styles.companyTable}>
                  <thead>
                    <tr>
                      <th>Empresa</th>
                      <th>QR Codes</th>
                      <th>7 dias</th>
                      <th>Total</th>
                      <th>Último acesso</th>
                      <th>Ações</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dados.empresas.map((empresa) => (
                      <tr key={empresa.id}>
                        <td>
                          {empresaEditandoId === empresa.id ? (
                            <form className={styles.inlineForm} onSubmit={salvarNomeEmpresa}>
                              <input
                                value={nomeEditado}
                                onChange={(event) => setNomeEditado(event.target.value)}
                                minLength={2}
                                maxLength={120}
                                aria-label={`Novo nome de ${empresa.nome}`}
                                required
                              />
                              <button type="submit" disabled={processandoAcao}>
                                Salvar
                              </button>
                            </form>
                          ) : (
                            <>
                              <strong>{empresa.nome}</strong>
                              <small>ID {empresa.id}</small>
                              <button
                                className={styles.textButton}
                                onClick={() => {
                                  setEmpresaEditandoId(empresa.id);
                                  setNomeEditado(empresa.nome);
                                }}
                              >
                                Editar nome
                              </button>
                            </>
                          )}
                        </td>
                        <td>
                          <div className={styles.qrList}>
                            <strong>
                              {empresa.qr_codes_ativos}/{empresa.total_qr_codes} ativos
                            </strong>
                            {empresa.qr_codes.map((qrCode) => (
                              <div className={styles.qrItem} key={qrCode.id}>
                                <span className={styles.qrCodeLabel}>{qrCode.codigo}</span>
                                <div className={styles.qrActions}>
                                  <button
                                    className={styles.textButton}
                                    onClick={() => mostrarQr(qrCode)}
                                    disabled={processandoAcao}
                                  >
                                    Mostrar QR
                                  </button>
                                  <button
                                    className={styles.textButton}
                                    onClick={() => {
                                      setQrEditandoCodigo(qrCode.codigo);
                                      setDestinoEditado(qrCode.destino_url);
                                    }}
                                  >
                                    Editar link
                                  </button>
                                  <button
                                    className={styles.textButton}
                                    onClick={() => alternarQr(qrCode)}
                                    disabled={processandoAcao}
                                  >
                                    {qrCode.ativo ? "Desativar" : "Ativar"}
                                  </button>
                                </div>
                                {qrEditandoCodigo === qrCode.codigo && (
                                  <form
                                    className={styles.inlineQrForm}
                                    onSubmit={salvarDestinoQr}
                                  >
                                    <input
                                      type="url"
                                      value={destinoEditado}
                                      onChange={(event) => setDestinoEditado(event.target.value)}
                                      aria-label={`Novo link de ${qrCode.codigo}`}
                                      required
                                    />
                                    <button type="submit" disabled={processandoAcao}>
                                      Salvar link
                                    </button>
                                  </form>
                                )}
                              </div>
                            ))}
                          </div>
                        </td>
                        <td>{empresa.acessos_ultimos_7_dias}</td>
                        <td>{empresa.total_acessos}</td>
                        <td>{formatarData(empresa.ultimo_acesso)}</td>
                        <td>
                          <span
                            className={
                              empresa.qr_codes_ativos > 0
                                ? styles.activeTag
                                : styles.inactiveTag
                            }
                          >
                            {empresa.qr_codes_ativos > 0 ? "Ativa" : "Sem QR"}
                          </span>
                          <button
                            className={styles.dangerButton}
                            onClick={() => iniciarNovoPeriodo(empresa.id, empresa.nome)}
                            disabled={processandoAcao}
                          >
                            Iniciar novo período
                          </button>
                          <button
                            className={styles.dangerButton}
                            onClick={() => arquivarEmpresa(empresa.id, empresa.nome)}
                            disabled={processandoAcao}
                          >
                            Arquivar
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <aside className={styles.qrPreview} aria-label="Visualização do QR Code">
                <p className={styles.eyebrow}>Visualização</p>
                <h3>{qrSelecionado ? qrSelecionado.codigo : "Selecione um QR"}</h3>
                {qrImagemUrl && qrSelecionado ? (
                  <>
                    <Image
                      src={qrImagemUrl}
                      alt={`QR Code ${qrSelecionado.codigo}`}
                      width={150}
                      height={150}
                      unoptimized
                    />
                    <a
                      className={styles.downloadButton}
                      href={qrImagemUrl}
                      download={`qr_${qrSelecionado.codigo}.png`}
                    >
                      Baixar PNG
                    </a>
                    <span>{qrSelecionado.url_publica}</span>
                  </>
                ) : (
                  <p className={styles.emptyState}>
                    Clique em “Mostrar QR” para visualizar e baixar a imagem.
                  </p>
                )}
              </aside>
            </div>
            {mensagemGerenciamento && (
              <p className={styles.actionMessage} role="status">
                {mensagemGerenciamento}
              </p>
            )}
          </article>

          <article className={styles.panel}>
            <div className={styles.panelHeading}>
              <div>
                <p className={styles.eyebrow}>Histórico operacional</p>
                <h2>Empresas arquivadas</h2>
              </div>
              <span className={styles.totalLabel}>
                {dados.empresas_arquivadas.length} empresas
              </span>
            </div>

            {dados.empresas_arquivadas.length === 0 ? (
              <p className={styles.emptyState}>Nenhuma empresa arquivada.</p>
            ) : (
              <div className={styles.archivedList}>
                {dados.empresas_arquivadas.map((empresa) => (
                  <div className={styles.archivedRow} key={empresa.id}>
                    <div>
                      <strong>{empresa.nome}</strong>
                      <span>
                        {empresa.total_qr_codes} QR/NFC · arquivada em {formatarData(empresa.arquivada_em)}
                      </span>
                    </div>
                    <button
                      className={styles.restoreButton}
                      onClick={() => restaurarEmpresa(empresa.id, empresa.nome)}
                      disabled={processandoAcao}
                    >
                      Restaurar
                    </button>
                  </div>
                ))}
              </div>
            )}
            <p className={styles.operationNote}>
              Arquivar pausa o redirecionamento dos QR Codes e tags NFC. O histórico de acessos permanece guardado.
            </p>
          </article>

          <article className={styles.panel}>
            <div className={styles.panelHeading}>
              <div>
                <p className={styles.eyebrow}>Monitoramento</p>
                <h2>Acessos recentes</h2>
              </div>
              <span className={styles.totalLabel}>Últimos 12</span>
            </div>

            {dados.acessos_recentes.length === 0 ? (
              <p className={styles.emptyState}>Ainda não há acessos registrados.</p>
            ) : (
              <div className={styles.accessList}>
                {dados.acessos_recentes.map((acesso, index) => (
                  <div
                    className={styles.accessRow}
                    key={`${acesso.acessado_em}-${acesso.codigo}-${index}`}
                  >
                    <span
                      className={`${styles.sourceIcon} ${
                        acesso.origem === "nfc" ? styles.nfcIcon : ""
                      }`}
                    >
                      {acesso.origem === "nfc" ? "NFC" : "QR"}
                    </span>
                    <div>
                      <strong>{acesso.empresa}</strong>
                      <span>{acesso.codigo}</span>
                    </div>
                    <time dateTime={acesso.acessado_em}>
                      {formatarData(acesso.acessado_em)}
                    </time>
                  </div>
                ))}
              </div>
            )}
          </article>
        </section>
      </section>
    </main>
  );
}
