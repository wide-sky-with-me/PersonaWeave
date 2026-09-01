import type { Config } from 'tailwindcss';

export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        /* ── Surface Hierarchy ── */
        background:        '#0c0c0f',   // deepest canvas
        surface:           '#111115',   // primary panel
        'surface-2':       '#17171c',   // slightly elevated
        'surface-3':       '#1e1e24',   // card / input
        'surface-hover':   '#25252c',   // interactive hover
        'surface-active':  '#2d2d36',   // pressed / selected

        /* ── One-pixel Borders ── */
        border:            'rgba(255,255,255,0.07)',
        'border-md':       'rgba(255,255,255,0.12)',
        'border-strong':   'rgba(255,255,255,0.20)',

        /* ── Accent – Indigo (primary action) ── */
        primary:        '#5b5fef',
        'primary-hover':  '#4e52d6',
        'primary-muted':  'rgba(91,95,239,0.15)',
        'primary-border': 'rgba(91,95,239,0.35)',

        /* ── Accent – Cyan (secondary / info) ── */
        accent:        '#06b6d4',
        'accent-hover':  '#0891b2',
        'accent-muted':  'rgba(6,182,212,0.12)',
        'accent-border': 'rgba(6,182,212,0.30)',

        /* ── Semantic Status Palette ── */
        ok:            '#22c55e',
        'ok-muted':    'rgba(34,197,94,0.12)',
        'ok-border':   'rgba(34,197,94,0.30)',
        warn:          '#f59e0b',
        'warn-muted':  'rgba(245,158,11,0.12)',
        'warn-border': 'rgba(245,158,11,0.30)',
        err:           '#ef4444',
        'err-muted':   'rgba(239,68,68,0.12)',
        'err-border':  'rgba(239,68,68,0.30)',
        info:          '#818cf8',
        'info-muted':  'rgba(129,140,248,0.12)',
        'info-border': 'rgba(129,140,248,0.30)',

        /* ── Legacy compat ── */
        cyan:    { neon: '#06b6d4', glow: 'rgba(6,182,212,0.20)' },
        emerald: { neon: '#22c55e', glow: 'rgba(34,197,94,0.20)'  },
      },

      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'Menlo', 'Consolas', 'monospace'],
      },

      fontSize: {
        '2xs': ['10px', { lineHeight: '14px' }],
      },

      borderRadius: {
        sm:   '4px',
        DEFAULT: '6px',
        md:   '8px',
        lg:   '10px',
        xl:   '12px',
        '2xl':'16px',
        '3xl':'20px',
      },

      boxShadow: {
        'panel':    '0 1px 3px rgba(0,0,0,0.4), 0 0 0 1px rgba(255,255,255,0.07)',
        'card':     '0 2px 8px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.07)',
        'elevated': '0 4px 20px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.10)',
        'primary':  '0 2px 12px rgba(91,95,239,0.25)',
        'cyan':     '0 2px 12px rgba(6,182,212,0.20)',
        'inset':    'inset 0 1px 2px rgba(0,0,0,0.4)',
      },

      animation: {
        'fade-in':    'fadeIn 150ms ease-out',
        'slide-up':   'slideUp 200ms ease-out',
        'spin-slow':  'spin 2s linear infinite',
        'pulse-dot':  'pulseDot 2s cubic-bezier(0.4,0,0.6,1) infinite',
        'shimmer':    'shimmer 1.5s infinite',
      },

      keyframes: {
        fadeIn:   { from: { opacity: '0' },              to: { opacity: '1' } },
        slideUp:  { from: { opacity: '0', transform: 'translateY(6px)' }, to: { opacity: '1', transform: 'translateY(0)' } },
        pulseDot: { '0%,100%': { opacity: '0.6', transform: 'scale(1)' }, '50%': { opacity: '1', transform: 'scale(1.2)' } },
        shimmer:  { '100%': { transform: 'translateX(100%)' } },
      },
    },
  },
  plugins: [],
} satisfies Config;
