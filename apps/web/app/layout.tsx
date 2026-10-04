import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "@/components/Providers";
import { Footer, Header } from "@/components/Shared";

export const metadata: Metadata = {
  title: "Constellation — mechanism-first disease atlas",
  description: "Evidence-backed research navigation across rare-disease mechanisms.",
};
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><Providers><Header />{children}<Footer /></Providers></body></html>;
}
