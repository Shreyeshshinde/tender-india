import { NextResponse, type NextRequest } from "next/server";

// Optional password for the whole website (pages, buttons and /api/*).
// Set SITE_PASSWORD (and optionally SITE_USER, default "admin") on the server to turn it on.
// Without it the site is open, so anyone could press Delete and Refresh.
export function middleware(req: NextRequest) {
  const password = process.env.SITE_PASSWORD;
  if (!password) return NextResponse.next();
  const user = process.env.SITE_USER || "admin";

  const auth = req.headers.get("authorization") || "";
  if (auth.startsWith("Basic ")) {
    try {
      const decoded = atob(auth.slice(6));
      const i = decoded.indexOf(":");
      if (i >= 0 && decoded.slice(0, i) === user && decoded.slice(i + 1) === password) {
        return NextResponse.next();
      }
    } catch {
      /* bad header: ask again */
    }
  }
  return new NextResponse("Login required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="Tender India", charset="UTF-8"' },
  });
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
