import assert from "node:assert/strict";
import { test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import {
  createConnectionActionButton,
  disconnectAndReconcileStatus,
  getGoogleBusinessLocationLabel,
  getConnectionAction,
  reconcileConnectionStatusAfterConflict,
} from "../src/lib/google-business-connection-state.ts";

const statusConectado = {
  conectado: true,
  status: "connected",
  local: { nome: "accounts/1/locations/10", titulo: "Loja A" },
};

test("locais com mesmo título são distinguidos pelo recurso Google já disponível", () => {
  const locais = [
    { nome: "locations/123", titulo: "Minha Loja", store_code: null },
    { nome: "locations/456", titulo: "Minha Loja", store_code: null },
    { nome: "locations/789", titulo: "Outra Loja", store_code: null },
  ];

  assert.equal(
    getGoogleBusinessLocationLabel(locais[0], locais),
    "Minha Loja (locations/123)",
  );
  assert.equal(
    getGoogleBusinessLocationLabel(locais[1], locais),
    "Minha Loja (locations/456)",
  );
  assert.equal(getGoogleBusinessLocationLabel(locais[2], locais), "Outra Loja");
});

test("a 409 que exige reautorização atualiza o estado e mostra o botão correto", async () => {
  const statusReautorizacao = {
    conectado: false,
    status: "reauth_required",
    local: null,
  };

  const status = await reconcileConnectionStatusAfterConflict(
    statusConectado,
    409,
    async () => statusReautorizacao,
  );
  const action = getConnectionAction(status);
  const markup = renderToStaticMarkup(
    createConnectionActionButton({
      action,
      isProcessing: false,
      onClick: () => {},
      disabled: false,
      className: "primary",
    }),
  );

  assert.equal(action, "reconnect");
  assert.match(markup, /<button[^>]*>Reconectar conta Google<\/button>/);
});

test("429 não refaz consulta de status nem muda a ação de conexão", async () => {
  let consultasDeStatus = 0;

  const status = await reconcileConnectionStatusAfterConflict(
    statusConectado,
    429,
    async () => {
      consultasDeStatus += 1;
      return {
        conectado: false,
        status: "reauth_required",
        local: null,
      };
    },
  );

  assert.equal(status, statusConectado);
  assert.equal(consultasDeStatus, 0);
  assert.equal(getConnectionAction(status), null);
});

test("se a releitura após 409 falhar, descarta o conectado antigo e oferece nova verificação", async () => {
  const status = await reconcileConnectionStatusAfterConflict(
    statusConectado,
    409,
    async () => {
      throw new Error("API indisponível");
    },
  );

  assert.deepEqual(status, {
    conectado: false,
    status: "unknown",
    local: null,
  });
  assert.equal(getConnectionAction(status), "retry");
  const markup = renderToStaticMarkup(
    createConnectionActionButton({
      action: "retry",
      isProcessing: false,
      onClick: () => {},
      disabled: false,
      className: "primary",
    }),
  );
  assert.match(markup, /<button[^>]*>Verificar conexão<\/button>/);
});

test("DELETE 502 seguido de status reauth_required atualiza a ação para reconectar", async () => {
  const chamadas = [];
  const resultado = await disconnectAndReconcileStatus(
    async () => {
      chamadas.push("DELETE");
      throw Object.assign(new Error("Resposta do servidor incerta"), { status: 502 });
    },
    async () => {
      chamadas.push("GET status");
      return {
        conectado: false,
        status: "reauth_required",
        local: null,
      };
    },
  );

  assert.deepEqual(chamadas, ["DELETE", "GET status"]);
  assert.equal(resultado.kind, "failed");
  assert.deepEqual(resultado.status, {
    conectado: false,
    status: "reauth_required",
    local: null,
  });
  const action = getConnectionAction(resultado.status);
  assert.equal(action, "reconnect");
  const markup = renderToStaticMarkup(
    createConnectionActionButton({
      action,
      isProcessing: false,
      onClick: () => {},
      disabled: false,
      className: "primary",
    }),
  );
  assert.match(markup, /<button[^>]*>Reconectar conta Google<\/button>/);
});

test("DELETE 502 e falha na releitura descartam conexão antiga e oferecem verificação", async () => {
  const resultado = await disconnectAndReconcileStatus(
    async () => {
      throw Object.assign(new Error("Resposta perdida"), { status: 502 });
    },
    async () => {
      throw new Error("API indisponível");
    },
  );

  assert.equal(resultado.kind, "failed");
  assert.deepEqual(resultado.status, {
    conectado: false,
    status: "unknown",
    local: null,
  });
  assert.equal(getConnectionAction(resultado.status), "retry");
});

test("DELETE 429 relê o backend em vez de presumir que a conexão permaneceu ativa", async () => {
  let consultasDeStatus = 0;
  const resultado = await disconnectAndReconcileStatus(
    async () => {
      throw Object.assign(new Error("Erro HTTP 429"), { status: 429 });
    },
    async () => {
      consultasDeStatus += 1;
      return {
        conectado: false,
        status: "reauth_required",
        local: null,
      };
    },
  );

  assert.equal(resultado.kind, "failed");
  assert.equal(resultado.status.status, "reauth_required");
  assert.equal(consultasDeStatus, 1);
});

test("DELETE concluído marca a conexão como encerrada sem consulta adicional", async () => {
  let consultasDeStatus = 0;
  const resultado = await disconnectAndReconcileStatus(
    async () => ({ conectado: false }),
    async () => {
      consultasDeStatus += 1;
      return statusConectado;
    },
  );

  assert.deepEqual(resultado, {
    kind: "disconnected",
    status: { conectado: false, status: "not_connected", local: null },
  });
  assert.equal(consultasDeStatus, 0);
});
