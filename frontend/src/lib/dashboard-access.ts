export type DashboardRole = "admin" | "client";

export function shouldRenderGoogleBusinessConnection(
  role: DashboardRole | null,
): boolean {
  return role === "client";
}
