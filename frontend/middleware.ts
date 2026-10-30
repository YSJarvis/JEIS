import { jwtVerify } from "jose";
import { NextResponse, type NextRequest } from "next/server";

/**
 * Route guard. Verifies the HTTP-only session cookie set by the Flask API and
 * sends each role to the part of the app it may use. The API enforces the same
 * rules on every request; this layer keeps users from loading pages they
 * cannot use and redirects anyone without a session to /login.
 */

const COOKIE = "ve_token";
const DEV_SECRET = "dev-only-insecure-secret-change-me-voterengine-2026";
const secret = process.env.JWT_SECRET ?? (process.env.NODE_ENV === "production" ? "" : DEV_SECRET);

type Role = "ADMIN" | "MANAGER" | "CADRE";
const HOME: Record<Role, string> = { ADMIN: "/dashboard", MANAGER: "/dashboard", CADRE: "/field" };
const RULES: { prefix: string; roles: Role[] }[] = [
  { prefix: "/field", roles: ["CADRE"] },
  { prefix: "/", roles: ["ADMIN", "MANAGER"] },   // dashboard and everything else
];

async function roleFrom(req: NextRequest): Promise<Role | null> {
  const token = req.cookies.get(COOKIE)?.value;
  if (!token || !secret) return null;
  try {
    const { payload } = await jwtVerify(token, new TextEncoder().encode(secret), { algorithms: ["HS256"] });
    return (payload.role as Role) ?? null;
  } catch {
    return null; // expired or tampered
  }
}

export async function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  const role = await roleFrom(req);

  if (pathname === "/login") {
    // already signed in: skip the form
    return role ? NextResponse.redirect(new URL(HOME[role], req.url)) : NextResponse.next();
  }
  if (!role) {
    const url = new URL("/login", req.url);
    if (pathname !== "/") url.searchParams.set("next", pathname + search);
    const res = NextResponse.redirect(url);
    if (req.cookies.has(COOKIE)) res.cookies.delete(COOKIE); // clear an expired/invalid cookie
    return res;
  }
  const rule = RULES.find((r) => pathname === r.prefix || pathname.startsWith(r.prefix === "/" ? "/" : r.prefix + "/"));
  if (rule && !rule.roles.includes(role)) {
    return NextResponse.redirect(new URL(HOME[role], req.url));
  }
  return NextResponse.next();
}

export const config = {
  // pages only: API calls are proxied to Flask, which does its own checks
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|sw.js|manifest.webmanifest|voters_template.csv).*)"],
};
