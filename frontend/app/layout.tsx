import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Tender India",
  description: "Security screening and ground search tenders from government portals across India",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <div className="wrap topbar-inner">
            <Link href="/" className="brand">
              <span className="brand-mark">TI</span> Tender India
            </Link>
            <span className="tagline">Security & ground search tenders across India</span>
          </div>
        </header>
        <main className="wrap">{children}</main>
        <footer className="wrap footer">
          Data collected from public government portals such as eprocure.gov.in. Not an official
          government website. Always confirm details on the official portal before bidding.
        </footer>
      </body>
    </html>
  );
}
