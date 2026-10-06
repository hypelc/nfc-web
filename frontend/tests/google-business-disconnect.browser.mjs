import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createServer } from "node:net";
import { setTimeout as delay } from "node:timers/promises";
import path from "node:path";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const frontendRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const fakeSession = {
  access_token: `eyJhbGciOiJub25lIn0.${Buffer.from(
    JSON.stringify({
      sub: "test-user",
      aud: "authenticated",
      role: "authenticated",
      exp: 4102444800,
    }),
  ).toString("base64url")}.test-signature`,
  refresh_token: "test-refresh-token",
  token_type: "bearer",
  expires_in: 2000000000,
  expires_at: 4102444800,
  user: {
    id: "test-user",
    aud: "authenticated",
    role: "authenticated",
    email: "test@example.test",
    app_metadata: { provider: "email", providers: ["email"] },
    user_metadata: {},
    created_at: "2026-01-01T00:00:00Z",
  },
};

let appProcess;
let browser;
let appUrl;
let appOutput = "";

async function findFreePort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert.ok(address && typeof address === "object");
  await new Promise((resolve, reject) =>
    server.close((error) => (error ? reject(error) : resolve())),
  );
  return address.port;
}

async function waitForApp() {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    if (appProcess.exitCode !== null) {
      throw new Error(`Frontend terminou antes de iniciar.\n${appOutput}`);
    }
    try {
      const response = await fetch(`${appUrl}/dashboard`);
      if (response.ok) return;
    } catch {
      // Aguarda o servidor local ficar pronto.
    }
    await delay(300);
  }
  throw new Error(`Frontend não iniciou no prazo.\n${appOutput}`);
}

before(async () => {
  const port = await findFreePort();
  appUrl = `http://127.0.0.1:${port}`;
  appProcess = spawn(
    "npm",
    ["run", "dev", "--", "--hostname", "127.0.0.1", "--port", String(port)],
    {
      cwd: frontendRoot,
      env: {
        ...process.env,
        NEXT_PUBLIC_API_URL: "https://api.test",
        NEXT_PUBLIC_SUPABASE_URL: "https://test-project.supabase.co",
        NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: "test-publishable-key",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  appProcess.stdout.setEncoding("utf8");
  appProcess.stderr.setEncoding("utf8");
  for (const stream of [appProcess.stdout, appProcess.stderr]) {
    stream.on("data", (chunk) => {
      appOutput = `${appOutput}${chunk}`.slice(-8000);
    });
  }

  await waitForApp();
  browser = await chromium.launch({ headless: true });
});

after(async () => {
  await browser?.close();
  if (appProcess && appProcess.exitCode === null) {
    const stopped = once(appProcess, "exit");
    appProcess.kill("SIGTERM");
    await Promise.race([stopped, delay(5000)]);
    if (appProcess.exitCode === null) appProcess.kill("SIGKILL");
  }
});

async function openConnectedPanel(
  page,
  {
    afterDeleteStatus,
    deleteStatus = 502,
    locations = [
      {
        nome: "accounts/test/locations/10",
        titulo: "Local Fictício",
        store_code: null,
      },
    ],
  },
) {
  let statusReads = 0;
  const requests = [];
  await page.addInitScript((session) => {
    localStorage.setItem("sb-test-project-auth-token", JSON.stringify(session));
  }, fakeSession);
  await page.route("https://api.test/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    requests.push(`${method} ${url.pathname}`);

    if (url.pathname === "/dashboard/establishments") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          role: "client",
          empresas: [{ id: 7, nome: "Empresa Fictícia" }],
        }),
      });
    }
    if (url.pathname === "/dashboard/overview") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          empresa: { id: 7, nome: "Empresa Fictícia" },
          estatisticas: {
            acessos_hoje: 0,
            acessos_ultimos_7_dias: 0,
            total_acessos: 0,
            acessos_qr: 0,
            acessos_nfc: 0,
            ultimo_acesso: null,
          },
          qr_atual: null,
          acessos_recentes: [],
          serie_temporal: [],
        }),
      });
    }
    if (url.pathname === "/dashboard/google-business/status") {
      statusReads += 1;
      if (statusReads === 1) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            conectado: true,
            status: "connected",
            local: {
              nome: "accounts/test/locations/10",
              titulo: "Local Fictício",
            },
          }),
        });
      }
      if (afterDeleteStatus === "unavailable") {
        return route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Status temporariamente indisponível." }),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          conectado: false,
          status: "reauth_required",
          local: null,
        }),
      });
    }
    if (url.pathname === "/dashboard/google-business/locations") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          locais: locations,
        }),
      });
    }
    if (url.pathname === "/dashboard/google-business" && method === "DELETE") {
      return route.fulfill({
        status: deleteStatus,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Falha fictícia do servidor." }),
      });
    }
    return route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Rota fictícia não configurada." }),
    });
  });

  await page.goto(`${appUrl}/dashboard`);
  await page
    .getByRole("heading", { name: "Google Business Profile" })
    .waitFor({ timeout: 30000 });
  await page.getByText("Conectado", { exact: true }).waitFor();
  await page.getByText("Local atual:").waitFor();
  return { statusReads: () => statusReads, requests };
}

test("DELETE 502 seguido de status reauth_required remove controles antigos", async () => {
  const page = await browser.newPage();
  try {
    const mock = await openConnectedPanel(page, {
      afterDeleteStatus: "reauth_required",
    });
    assert.match(
      await page.getByText(/Esta ação desvincula somente esta empresa/).innerText(),
      /não revoga o acesso do aplicativo à sua Conta Google\./,
    );
    await page.getByRole("button", { name: "Desvincular esta empresa" }).click();
    await page.getByRole("button", { name: "Reconectar conta Google" }).waitFor();

    assert.equal(await page.getByText("Não conectado", { exact: true }).count(), 1);
    assert.equal(await page.getByText("Local atual:").count(), 0);
    assert.equal(await page.getByRole("button", { name: "Desvincular esta empresa" }).count(), 0);
    assert.equal(
      await page.locator('p[role="alert"]').innerText(),
      "Falha fictícia do servidor.",
    );
    assert.equal(mock.statusReads(), 2);
    assert.deepEqual(mock.requests.slice(-2), [
      "DELETE /dashboard/google-business",
      "GET /dashboard/google-business/status",
    ]);
  } finally {
    await page.close();
  }
});

test("DELETE 502 com status indisponível esconde conexão e permite verificar", async () => {
  const page = await browser.newPage();
  try {
    const mock = await openConnectedPanel(page, {
      afterDeleteStatus: "unavailable",
    });
    await page.getByRole("button", { name: "Desvincular esta empresa" }).click();
    await page.getByRole("button", { name: "Verificar conexão" }).waitFor();

    assert.equal(await page.getByText("Status indisponível", { exact: true }).count(), 1);
    assert.equal(await page.getByText("Local atual:").count(), 0);
    assert.equal(await page.getByRole("button", { name: "Desvincular esta empresa" }).count(), 0);
    assert.equal(mock.statusReads(), 2);
  } finally {
    await page.close();
  }
});

test("DELETE 429 relê status e remove controles se backend confirma desvinculação", async () => {
  const page = await browser.newPage();
  try {
    const mock = await openConnectedPanel(page, {
      afterDeleteStatus: "reauth_required",
      deleteStatus: 429,
    });
    await page.getByRole("button", { name: "Desvincular esta empresa" }).click();

    await page.locator('p[role="alert"]').waitFor();
    assert.equal(await page.getByText("Não conectado", { exact: true }).count(), 1);
    assert.equal(await page.getByText("Local atual:").count(), 0);
    assert.equal(await page.getByRole("button", { name: "Desvincular esta empresa" }).count(), 0);
    assert.equal(await page.getByRole("button", { name: "Reconectar conta Google" }).count(), 1);
    assert.equal(mock.statusReads(), 2);
  } finally {
    await page.close();
  }
});

test("seletor distingue locais com mesmo título pelo recurso Google", async () => {
  const page = await browser.newPage();
  try {
    await openConnectedPanel(page, {
      afterDeleteStatus: "reauth_required",
      locations: [
        { nome: "locations/10", titulo: "Minha Loja", store_code: null },
        { nome: "locations/20", titulo: "Minha Loja", store_code: null },
      ],
    });

    assert.deepEqual(
      await page.locator("select option").allTextContents(),
      [
        "Escolha um local",
        "Minha Loja (locations/10)",
        "Minha Loja (locations/20)",
      ],
    );
    assert.deepEqual(
      await page.locator("select option").evaluateAll((options) =>
        options.slice(1).map((option) => option.value),
      ),
      ["locations/10", "locations/20"],
    );
  } finally {
    await page.close();
  }
});
