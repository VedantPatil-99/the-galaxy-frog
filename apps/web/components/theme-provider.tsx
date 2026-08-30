"use client"

import type { ComponentProps } from "react"
import { ThemeProvider as NextThemesProvider } from "next-themes"

type ThemeProviderProps = ComponentProps<typeof NextThemesProvider>

function ThemeProvider({ children, ...props }: ThemeProviderProps) {
  return <NextThemesProvider {...props}>{children}</NextThemesProvider>
}

export { ThemeProvider }
