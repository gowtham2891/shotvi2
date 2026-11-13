/** @type {import('tailwindcss').Config} */
module.exports = {
    darkMode: ["class"],
    content: [
    "./src/**/*.{js,jsx,ts,tsx}",
    "./index.html"
  ],
  theme: {
    extend: {
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)'
      },
      colors: {
        k: {
          bg: 'rgb(var(--k-bg) / <alpha-value>)',
          surface: 'rgb(var(--k-surface) / <alpha-value>)',
          raised: 'rgb(var(--k-raised) / <alpha-value>)',
          line: 'rgb(var(--k-line) / <alpha-value>)',
          line2: 'rgb(var(--k-line2) / <alpha-value>)',
          text: 'rgb(var(--k-text) / <alpha-value>)',
          soft: 'rgb(var(--k-soft) / <alpha-value>)',
          muted: 'rgb(var(--k-muted) / <alpha-value>)',
          subtle: 'rgb(var(--k-subtle) / <alpha-value>)',
          faint: 'rgb(var(--k-faint) / <alpha-value>)',
          accent: 'rgb(var(--k-accent) / <alpha-value>)',
          accent2: 'rgb(var(--k-accent2) / <alpha-value>)',
          accentsoft: 'rgb(var(--k-accentsoft) / <alpha-value>)',
          ink: 'rgb(var(--k-ink) / <alpha-value>)',
          mark: 'rgb(var(--k-mark) / <alpha-value>)',
          ok: 'rgb(var(--k-ok) / <alpha-value>)',
          warn: 'rgb(var(--k-warn) / <alpha-value>)',
          bad: 'rgb(var(--k-bad) / <alpha-value>)',
        },
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))'
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))'
        },
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))'
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))'
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))'
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))'
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))'
        },
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        chart: {
          '1': 'hsl(var(--chart-1))',
          '2': 'hsl(var(--chart-2))',
          '3': 'hsl(var(--chart-3))',
          '4': 'hsl(var(--chart-4))',
          '5': 'hsl(var(--chart-5))'
        }
      },
      keyframes: {
        'accordion-down': {
          from: {
            height: '0'
          },
          to: {
            height: 'var(--radix-accordion-content-height)'
          }
        },
        'accordion-up': {
          from: {
            height: 'var(--radix-accordion-content-height)'
          },
          to: {
            height: '0'
          }
        }
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out'
      }
    }
  },
  plugins: [require("tailwindcss-animate")],
};