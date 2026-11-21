import type { Metadata } from "next";
import localFont from "next/font/local";

import { ThemeProvider } from "@/components/theme-provider";
import { cn } from "@/lib/utils";
import "./globals.css";

const commissioner = localFont({
  src: "./fonts/commissioner-latin.woff2",
  variable: "--font-commissioner",
  weight: "100 900",
  display: "swap",
});

const doto = localFont({
  src: "./fonts/doto-latin-800.woff2",
  variable: "--font-doto",
  weight: "800",
  display: "swap",
  adjustFontFallback: false,
});

export const metadata: Metadata = {
  applicationName: "Galaxy Frog",
  title: {
    default: "Galaxy Frog",
    template: "%s | Galaxy Frog",
  },
  description:
    "Temporal multimodal retrieval for timestamp-grounded video answers.",
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
    title: "Galaxy Frog",
  },
};


export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={cn(
        "h-full antialiased",
        commissioner.variable,
        doto.variable,
      )}
    >
      <body className="flex min-h-full flex-col">
        <ThemeProvider
          attribute="class"
          defaultTheme="system"
          enableSystem
          disableTransitionOnChange
        >
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
