import assert from "node:assert/strict";
import { test } from "node:test";
import { shouldRenderGoogleBusinessConnection } from "../src/lib/dashboard-access.ts";

test("only client dashboards render the Google connection panel", () => {
  assert.equal(shouldRenderGoogleBusinessConnection("client"), true);
  assert.equal(shouldRenderGoogleBusinessConnection("admin"), false);
  assert.equal(shouldRenderGoogleBusinessConnection(null), false);
});
