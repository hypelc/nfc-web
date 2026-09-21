"use client";

import { useId } from "react";
import {
  calculateWeeklyTrend,
  formatAccesses,
  formatAverage,
  formatWeekdayDate,
  type AccessSeries,
} from "@/lib/dashboard-report";
import styles from "./WeeklyTrend.module.css";

function joinNames(names: string[]) {
  if (names.length <= 1) return names[0] ?? "";
  if (names.length === 2) return `${names[0]} e ${names[1]}`;
  return `${names.slice(0, -1).join(", ")} e ${names[names.length - 1]}`;
}

export default function WeeklyTrend({
  series,
  statsStartAt,
}: {
  series: AccessSeries | null;
  statsStartAt?: string | null;
}) {
  const id = useId();
  const trend = series
    ? calculateWeeklyTrend(series["30_dias"], statsStartAt)
    : null;
  const maximumAverage = trend
    ? Math.max(...trend.rows.map((row) => row.media), 1)
    : 1;

  return (
    <section className={styles.panel} aria-labelledby={`${id}-title`}>
      <p className={styles.eyebrow}>Leitura do período</p>
      <div className={styles.heading}>
        <div>
          <h2 id={`${id}-title`}>Tendência semanal — últimos 30 dias</h2>
          <p className={styles.subtitle}>
            Comparação pela média de acessos em cada ocorrência completa do dia
            da semana.
          </p>
        </div>
      </div>

      {!trend ? (
        <div className={styles.state} role="status">
          <strong>Tendência indisponível no momento</strong>
          <p>
            A série de 30 dias não está disponível ou chegou incompleta. O
            gráfico diário continua separado deste estado.
          </p>
        </div>
      ) : trend.status === "insufficient" ? (
        <div className={styles.state} role="status">
          <strong>
            Ainda não há histórico suficiente para comparar os dias da semana
          </strong>
          <p>
            É preciso ter pelo menos 14 dias completos e elegíveis. O dia
            corrente e períodos parciais ficam fora desta comparação.
          </p>
        </div>
      ) : (
        <>
          <div
            className={`${styles.summary} ${trend.status === "no-movement" ? styles.summaryQuiet : ""}`}
          >
            {trend.status === "no-movement" ? (
              <p>
                <strong>
                  Não houve movimento suficiente para apontar um melhor dia.
                </strong>{" "}
                Os dias completos elegíveis não registraram acessos.
              </p>
            ) : (
              <>
                <p>
                  {trend.vencedores.length > 1
                    ? "Empate: "
                    : "Nos últimos 30 dias, "}
                  <strong>
                    {joinNames(trend.vencedores.map((row) => row.nome))}
                  </strong>{" "}
                  {trend.vencedores.length > 1 ? "apresentaram" : "apresentou"}{" "}
                  média de{" "}
                  <strong>
                    {formatAccesses(trend.vencedores[0].media, "average")}
                  </strong>{" "}
                  por ocorrência
                  {trend.vencedores.length === 1 && (
                    <>
                      {" "}
                      ({formatAccesses(trend.vencedores[0].total)} em{" "}
                      {trend.vencedores[0].ocorrencias}{" "}
                      {trend.vencedores[0].ocorrencias === 1
                        ? "ocorrência"
                        : "ocorrências"}
                      ).
                    </>
                  )}
                  {trend.vencedores.length > 1 && "."}
                </p>
                {trend.vencedores.length > 1 && (
                  <p className={styles.tieContext}>
                    Cada dia empatado teve a mesma média; os totais e
                    ocorrências estão detalhados abaixo.
                  </p>
                )}
              </>
            )}
          </div>

          <div className={styles.detailsGrid}>
            <div className={styles.bestDate}>
              <span className={styles.label}>Melhor data individual</span>
              {trend.melhorData ? (
                <>
                  <strong>
                    {formatWeekdayDate(trend.melhorData.data, "full")}
                  </strong>
                  <span>{formatAccesses(trend.melhorData.acessos)}</span>
                </>
              ) : (
                <strong>Nenhuma data com movimento</strong>
              )}
            </div>
            <div className={styles.bestDate}>
              <span className={styles.label}>Média geral por dia completo</span>
              <strong>{formatAccesses(trend.mediaGeral, "average")}</strong>
              <span>{trend.elegiveis.length} dias completos e elegíveis</span>
            </div>
          </div>

          <div
            className={styles.ranking}
            aria-label="Ranking dos dias da semana por média de acessos"
          >
            <div className={styles.rankingHeading}>
              <h3>Ranking por média</h3>
              <span>Média · total · ocorrências</span>
            </div>
            <ol>
              {trend.rows.map((row, index) => (
                <li
                  key={row.weekday}
                  className={
                    trend.vencedores.includes(row) ? styles.winner : ""
                  }
                >
                  <div className={styles.rowTopline}>
                    <span className={styles.position}>{index + 1}</span>
                    <strong>{row.nome}</strong>
                    <span className={styles.average}>
                      {formatAverage(row.media)} / ocorrência
                    </span>
                  </div>
                  <div className={styles.barTrack} aria-hidden="true">
                    <span
                      style={{
                        width: `${(row.media / maximumAverage) * 100}%`,
                      }}
                    />
                  </div>
                  <p>
                    {formatAccesses(row.total)} em {row.ocorrencias}{" "}
                    {row.ocorrencias === 1 ? "ocorrência" : "ocorrências"}
                  </p>
                </li>
              ))}
            </ol>
          </div>
        </>
      )}

      <p className={styles.note}>
        Tendência calculada somente com acessos disponíveis em dias completos. O
        dia corrente, períodos parciais e datas anteriores ao início da medição
        não entram no ranking.
      </p>
    </section>
  );
}
