import type { Metadata, Viewport } from "next";
import { Anek_Latin } from "next/font/google";
import "leaflet/dist/leaflet.css";
import "./globals.css";
import { AuthProvider } from "@/lib/auth";

const anek = Anek_Latin({ subsets: ["latin"], axes: ["wdth"], variable: "--font-anek", display: "swap" });

export const metadata: Metadata = {
  title: "VoterEngine AI",
  description: "Micro-targeting and cadre routing for field campaigns",
};
export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#5B2E91" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={anek.variable}>
      <body className="min-h-dvh"><AuthProvider>{children}</AuthProvider></body>
    </html>
  );
}
