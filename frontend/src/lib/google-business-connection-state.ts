import { createElement, type ReactElement } from "react";

export type GoogleBusinessConnectionStatus = {
  conectado: boolean;
  status:
    | "not_connected"
    | "connected"
    | "reauth_required"
    | "revoked"
    | "unknown";
  local: {
    nome: string;
    titulo: string;
  } | null;
};

export type GoogleBusinessLocationOption = {
  nome: string;
  titulo: string;
  store_code: string | null;
};

export function getGoogleBusinessLocationLabel(
  local: GoogleBusinessLocationOption,
  locais: GoogleBusinessLocationOption[],
): string {
  const tituloDuplicado =
    locais.filter((item) => item.titulo === local.titulo).length > 1;
  return tituloDuplicado ? `${local.titulo} (${local.nome})` : local.titulo;
}

export type GoogleBusinessConnectionAction =
  | "connect"
  | "reconnect"
  | "retry"
  | null;

export type DisconnectOutcome =
  | {
      kind: "disconnected";
      status: GoogleBusinessConnectionStatus;
    }
  | {
      kind: "failed";
      status: GoogleBusinessConnectionStatus | null;
      error: unknown;
    };

export function getConnectionAction(
  status: GoogleBusinessConnectionStatus | null,
): GoogleBusinessConnectionAction {
  if (status?.status === "unknown") return "retry";
  if (status?.status === "reauth_required") return "reconnect";
  if (status?.conectado !== true) return "connect";
  return null;
}

export async function reconcileConnectionStatusAfterConflict(
  currentStatus: GoogleBusinessConnectionStatus | null,
  errorStatus: number | undefined,
  loadStatus: () => Promise<GoogleBusinessConnectionStatus>,
): Promise<GoogleBusinessConnectionStatus | null> {
  if (errorStatus !== 409) return currentStatus;

  try {
    return await loadStatus();
  } catch {
    return {
      conectado: false,
      status: "unknown",
      local: null,
    };
  }
}

export async function disconnectAndReconcileStatus(
  disconnect: () => Promise<unknown>,
  loadStatus: () => Promise<GoogleBusinessConnectionStatus>,
): Promise<DisconnectOutcome> {
  try {
    await disconnect();
    return {
      kind: "disconnected",
      status: { conectado: false, status: "not_connected", local: null },
    };
  } catch (error) {
    let status: GoogleBusinessConnectionStatus;
    try {
      status = await loadStatus();
    } catch {
      status = { conectado: false, status: "unknown", local: null };
    }
    return { kind: "failed", status, error };
  }
}

export function createConnectionActionButton({
  action,
  isProcessing,
  onClick,
  disabled,
  className,
}: {
  action: Exclude<GoogleBusinessConnectionAction, null>;
  isProcessing: boolean;
  onClick: () => void;
  disabled: boolean;
  className: string;
}): ReactElement {
  const label = isProcessing
    ? action === "retry"
      ? "Verificando conexão..."
      : "Abrindo Google..."
    : action === "retry"
      ? "Verificar conexão"
      : action === "reconnect"
        ? "Reconectar conta Google"
        : "Conectar conta Google";

  return createElement(
    "button",
    { type: "button", className, onClick, disabled },
    label,
  );
}
