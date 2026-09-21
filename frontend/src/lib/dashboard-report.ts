export const REPORT_TIME_ZONE = "America/Sao_Paulo";

export type DailyAccess = { data: string; acessos: number };
export type AccessSeries = {
  "7_dias": DailyAccess[];
  "30_dias": DailyAccess[];
};
export type ReportMetrics = {
  acessos_hoje: number;
  acessos_ultimos_7_dias: number;
  total_acessos: number;
};

export type WeekdayTrendRow = {
  weekday: number;
  nome: string;
  abreviacao: string;
  ocorrencias: number;
  total: number;
  media: number;
};

export type WeeklyTrend = {
  status: "ready" | "insufficient" | "no-movement";
  rows: WeekdayTrendRow[];
  elegiveis: DailyAccess[];
  melhorData: (DailyAccess & { nomeDia: string }) | null;
  vencedores: WeekdayTrendRow[];
  mediaGeral: number;
};

const DAY_MS = 86_400_000;
const WEEKDAY_NAMES = [
  "segunda-feira",
  "terça-feira",
  "quarta-feira",
  "quinta-feira",
  "sexta-feira",
  "sábado",
  "domingo",
] as const;
const WEEKDAY_SHORT_NAMES = [
  "seg.",
  "ter.",
  "qua.",
  "qui.",
  "sex.",
  "sáb.",
  "dom.",
] as const;

function calendarDay(value: unknown): number | null {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return null;
  const timestamp = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(timestamp) &&
    new Date(timestamp).toISOString().slice(0, 10) === value
    ? timestamp
    : null;
}

function calendarParts(value: string): [number, number, number] | null {
  if (calendarDay(value) === null) return null;
  const [year, month, day] = value.split("-").map(Number);
  return [year, month, day];
}

// UTC is used only as a deterministic calendar calculator. The value is never
// formatted in a timezone, so a browser timezone cannot move the calendar day.
export function weekdayForCalendarDate(value: string): number | null {
  const parts = calendarParts(value);
  if (!parts) return null;
  const [year, month, day] = parts;
  return (new Date(Date.UTC(year, month - 1, day)).getUTCDay() + 6) % 7;
}

export function weekdayName(value: string, short = false): string {
  const weekday = weekdayForCalendarDate(value);
  if (weekday === null) return "dia indisponível";
  return short ? WEEKDAY_SHORT_NAMES[weekday] : WEEKDAY_NAMES[weekday];
}

function isDailySeries(value: unknown, length: number): value is DailyAccess[] {
  if (!Array.isArray(value) || value.length !== length) return false;
  let previous: number | null = null;
  return value.every((point: unknown) => {
    if (
      !point ||
      typeof point !== "object" ||
      !("data" in point) ||
      !("acessos" in point)
    )
      return false;
    const day = calendarDay(point.data);
    if (
      day === null ||
      typeof point.acessos !== "number" ||
      !Number.isSafeInteger(point.acessos) ||
      point.acessos < 0
    )
      return false;
    if (previous !== null && day - previous !== DAY_MS) return false;
    previous = day;
    return true;
  });
}

export function sumAccesses(points: DailyAccess[]): number {
  return points.reduce((total, point) => total + point.acessos, 0);
}

// Validate the API contract; never fill missing dates with invented zeros.
// Invalid series do not invalidate the other dashboard data.
export function readAccessSeries(
  value: unknown,
  metrics: ReportMetrics,
): AccessSeries | null {
  if (
    !value ||
    typeof value !== "object" ||
    !("7_dias" in value) ||
    !("30_dias" in value)
  )
    return null;
  const week = value["7_dias"];
  const month = value["30_dias"];
  if (!isDailySeries(week, 7) || !isDailySeries(month, 30)) return null;
  if (
    !week.every(
      (point, index) =>
        point.data === month[index + 23].data &&
        point.acessos === month[index + 23].acessos,
    )
  )
    return null;
  const weekTotal = sumAccesses(week);
  const monthTotal = sumAccesses(month);
  if (
    !Number.isSafeInteger(monthTotal) ||
    !Number.isSafeInteger(metrics.total_acessos)
  )
    return null;
  if (
    week[6].acessos !== metrics.acessos_hoje ||
    weekTotal !== metrics.acessos_ultimos_7_dias ||
    monthTotal > metrics.total_acessos
  )
    return null;
  return { "7_dias": week, "30_dias": month };
}

// Calendar dates from the API are already in Sao Paulo, not UTC instants.
export function formatCalendarDate(value: string, includeYear = false): string {
  if (calendarDay(value) === null) return "Data indisponível";
  const [year, month, day] = value.split("-");
  return `${day}/${month}${includeYear ? `/${year}` : ""}`;
}

export function formatWeekdayDate(
  value: string,
  style: "short" | "full" | "date" = "full",
): string {
  const date = formatCalendarDate(value, style === "full");
  if (date === "Data indisponível" || style === "date") return date;
  return `${weekdayName(value, style === "short")}, ${date}`;
}

export function formatReportTimestamp(
  value: string | null | undefined,
  dateOnly = false,
): string {
  if (!value) return "Nenhum acesso registrado";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "Data indisponível";
  return new Intl.DateTimeFormat("pt-BR", {
    timeZone: REPORT_TIME_ZONE,
    dateStyle: "medium",
    ...(dateOnly ? {} : { timeStyle: "short" as const }),
  }).format(date);
}

export function chartTicks(points: DailyAccess[]): number[] {
  const maximum = Math.max(1, ...points.map((point) => point.acessos));
  // Small series must show integer accesses, not fractional tick labels.
  const divisions = Math.min(4, maximum);
  const step = Math.ceil(maximum / divisions);
  return Array.from({ length: divisions + 1 }, (_, index) => index * step);
}

function localTimestampParts(value: string): {
  date: string;
  hour: number;
  minute: number;
  second: number;
} | null {
  const timestamp = new Date(value);
  if (!Number.isFinite(timestamp.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: REPORT_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(timestamp);
  const values: Record<string, string> = {};
  for (const part of parts) {
    if (part.type !== "literal") values[part.type] = part.value;
  }
  if (
    !values.year ||
    !values.month ||
    !values.day ||
    !values.hour ||
    !values.minute ||
    !values.second
  )
    return null;
  return {
    date: `${values.year}-${values.month}-${values.day}`,
    hour: Number(values.hour),
    minute: Number(values.minute),
    second: Number(values.second),
  };
}

export function formatAverage(value: number): string {
  return new Intl.NumberFormat("pt-BR", {
    maximumFractionDigits: 1,
  }).format(value);
}

export function formatAccesses(
  value: number,
  display: "integer" | "average" = "integer",
): string {
  const formatted =
    display === "average"
      ? formatAverage(value)
      : new Intl.NumberFormat("pt-BR").format(value);
  return `${formatted} ${formatted === "1" ? "acesso" : "acessos"}`;
}

export function calculateWeeklyTrend(
  points: DailyAccess[],
  statsStartAt?: string | null,
): WeeklyTrend {
  const currentDate = points.length
    ? points[points.length - 1].data
    : undefined;
  const start = statsStartAt ? localTimestampParts(statsStartAt) : null;
  const startIsPartial =
    start !== null &&
    (start.hour !== 0 || start.minute !== 0 || start.second !== 0);
  const elegiveis = points.filter((point) => {
    if (point.data === currentDate) return false;
    if (start && point.data < start.date) return false;
    if (start && startIsPartial && point.data === start.date) return false;
    return true;
  });

  const baseRows = WEEKDAY_NAMES.map((nome, weekday) => {
    const weekdayPoints = elegiveis.filter(
      (point) => weekdayForCalendarDate(point.data) === weekday,
    );
    const total = sumAccesses(weekdayPoints);
    return {
      weekday,
      nome,
      abreviacao: WEEKDAY_SHORT_NAMES[weekday],
      ocorrencias: weekdayPoints.length,
      total,
      media: weekdayPoints.length ? total / weekdayPoints.length : 0,
    };
  });
  const rows = [...baseRows].sort(
    (left, right) => right.media - left.media || left.weekday - right.weekday,
  );
  const mediaGeral = elegiveis.length
    ? sumAccesses(elegiveis) / elegiveis.length
    : 0;
  const melhorData =
    elegiveis.length && Math.max(...elegiveis.map((point) => point.acessos)) > 0
      ? elegiveis.reduce((best, point) =>
          point.acessos > best.acessos ? point : best,
        )
      : null;
  const melhorDataComDia = melhorData
    ? { ...melhorData, nomeDia: weekdayName(melhorData.data) }
    : null;
  const maxMedia = Math.max(
    ...rows.filter((row) => row.ocorrencias > 0).map((row) => row.media),
    0,
  );
  const vencedores =
    maxMedia > 0
      ? rows.filter(
          (row) =>
            row.ocorrencias > 0 && Math.abs(row.media - maxMedia) < 0.000001,
        )
      : [];
  const status =
    elegiveis.length < 14
      ? "insufficient"
      : maxMedia === 0
        ? "no-movement"
        : "ready";

  return {
    status,
    rows,
    elegiveis,
    melhorData: melhorDataComDia,
    vencedores,
    mediaGeral,
  };
}
