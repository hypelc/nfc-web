"use client";

import { useId, useState } from "react";
import {
  chartTicks,
  formatCalendarDate,
  formatWeekdayDate,
  sumAccesses,
  weekdayName,
  type AccessSeries,
} from "@/lib/dashboard-report";
import styles from "./AccessChart.module.css";

const numberFormat = new Intl.NumberFormat("pt-BR");

export default function AccessChart({
  series,
  period,
  onPeriodChange,
}: {
  series: AccessSeries | null;
  period: 7 | 30;
  onPeriodChange: (period: 7 | 30) => void;
}) {
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const id = useId();

  const points = series?.[`${period}_dias`];
  const selectedIndex =
    points?.findIndex((point) => point.data === selectedDay) ?? -1;
  const activeIndex =
    selectedIndex < 0 ? (points?.length ?? 1) - 1 : selectedIndex;
  const activePoint = points?.[activeIndex];
  const ticks = points ? chartTicks(points) : [0, 1];
  const maximum = ticks[ticks.length - 1];
  const total = points ? sumAccesses(points) : 0;
  const x = (index: number) => (index / ((points?.length ?? 2) - 1)) * 1000;
  const y = (count: number) => 220 - (count / maximum) * 220;
  const line = points
    ?.map((point, index) => `${x(index)},${y(point.acessos)}`)
    .join(" ");

  return (
    <section className={styles.panel} aria-labelledby={`${id}-title`}>
      <div className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>Seu movimento</p>
          <h2 id={`${id}-title`}>Acessos por dia</h2>
          <p className={styles.subtitle}>
            Veja como os acessos se distribuem ao longo do tempo.
          </p>
        </div>
        {points && (
          <div
            className={`${styles.periods} ${styles.noPrint}`}
            role="group"
            aria-label="Período do gráfico"
          >
            {([7, 30] as const).map((days) => (
              <button
                key={days}
                type="button"
                aria-pressed={period === days}
                onClick={() => {
                  onPeriodChange(days);
                  setSelectedDay(null);
                }}
              >
                {days} dias
              </button>
            ))}
          </div>
        )}
      </div>

      {!points || !activePoint ? (
        <div className={styles.unavailable} role="status">
          <strong>Gráfico indisponível no momento</strong>
          <p>
            A série diária não está disponível ou chegou incompleta. Os demais
            indicadores continuam disponíveis.
          </p>
        </div>
      ) : (
        <>
          <div className={styles.summary}>
            <div>
              <p className={styles.total}>
                <strong>{numberFormat.format(total)}</strong>{" "}
                {total === 1 ? "acesso" : "acessos"} no período
              </p>
              <p className={styles.dateRange}>
                {formatCalendarDate(points[0].data, true)} —{" "}
                {formatCalendarDate(points[points.length - 1].data, true)} ·
                inclui hoje
              </p>
            </div>
            <div className={`${styles.dayPicker} ${styles.noPrint}`}>
              <label htmlFor={`${id}-day`}>Consultar dia</label>
              <select
                id={`${id}-day`}
                value={activePoint.data}
                onChange={(event) => setSelectedDay(event.target.value)}
              >
                {points.map((point) => (
                  <option key={point.data} value={point.data}>
                    {formatWeekdayDate(point.data, "full")}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className={styles.chart}>
            <div
              className={styles.axis}
              style={{
                minWidth: `${numberFormat.format(maximum).length + 1}ch`,
              }}
              aria-hidden="true"
            >
              {ticks.map((tick) => (
                <span
                  key={tick}
                  style={{ bottom: `${(tick / maximum) * 100}%` }}
                >
                  {numberFormat.format(tick)}
                </span>
              ))}
            </div>
            <svg
              className={styles.plot}
              viewBox="-8 -8 1016 236"
              preserveAspectRatio="none"
              role="img"
              aria-labelledby={`${id}-chart-title ${id}-chart-description`}
              onPointerDown={(event) => {
                const bounds = event.currentTarget.getBoundingClientRect();
                const ratio =
                  (((event.clientX - bounds.left) / bounds.width) * 1016 - 8) /
                  1000;
                const index = Math.max(
                  0,
                  Math.min(
                    points.length - 1,
                    Math.round(ratio * (points.length - 1)),
                  ),
                );
                setSelectedDay(points[index].data);
              }}
            >
              <title id={`${id}-chart-title`}>
                Acessos diários nos últimos {period} dias
              </title>
              <desc id={`${id}-chart-description`}>
                Escala de zero a {maximum} acessos. {total} acessos no período.
                Consulte cada valor no seletor de dia ou na tabela abaixo.
              </desc>
              {ticks.map((tick) => (
                <line
                  key={tick}
                  className={styles.gridLine}
                  x1="0"
                  x2="1000"
                  y1={y(tick)}
                  y2={y(tick)}
                  vectorEffect="non-scaling-stroke"
                />
              ))}
              <polyline
                points={line}
                className={styles.dataLine}
                vectorEffect="non-scaling-stroke"
              />
              <line
                className={styles.marker}
                x1={x(activeIndex)}
                x2={x(activeIndex)}
                y1="0"
                y2="220"
                vectorEffect="non-scaling-stroke"
              />
              <path
                className={styles.dataLine}
                d={`M ${x(activeIndex) - 5} ${y(activePoint.acessos)} h 10`}
                vectorEffect="non-scaling-stroke"
              />
            </svg>
            <div className={styles.xAxis} aria-hidden="true">
              {[0, Math.floor((points.length - 1) / 2), points.length - 1].map(
                (index) => (
                  <span key={index}>
                    {period === 7
                      ? `${weekdayName(points[index].data, true)}, ${formatCalendarDate(points[index].data)}`
                      : formatCalendarDate(points[index].data)}
                  </span>
                ),
              )}
            </div>
          </div>

          <p
            className={styles.selectedDay}
            aria-live="polite"
            aria-atomic="true"
          >
            {formatWeekdayDate(activePoint.data, "full")} <span>·</span>{" "}
            <strong>
              {numberFormat.format(activePoint.acessos)}{" "}
              {activePoint.acessos === 1 ? "acesso" : "acessos"}
            </strong>
          </p>
          {total === 0 && (
            <p className={styles.empty}>
              Nenhum acesso registrado neste período. Os dias sem movimento
              aparecem com zero.
            </p>
          )}

          <details className={`${styles.details} ${styles.noPrint}`}>
            <summary>Ver valores por dia</summary>
            <div className={styles.tableContainer}>
              <table>
                <caption>
                  Acessos diários · últimos {period} dias · horário de São Paulo
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Data</th>
                    <th scope="col">Acessos</th>
                  </tr>
                </thead>
                <tbody>
                  {points.map((point) => (
                    <tr key={point.data}>
                      <th scope="row">
                        {formatWeekdayDate(point.data, "full")}
                      </th>
                      <td>{numberFormat.format(point.acessos)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
      <p className={styles.note}>
        Horário de São Paulo. Acessos podem incluir visitas repetidas; não
        representam pessoas únicas nem avaliações concluídas.
      </p>
    </section>
  );
}
