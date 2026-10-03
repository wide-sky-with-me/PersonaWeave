import type { Config } from 'tailwindcss';

export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        /* ── Surface Hierarchy ── */
        background: '#0c0c0f', // deepest canvas
        surface: '#111115', // primary panel
        'surface-1': '#141418', // slightly elevated card/strip
        'surface-2': '#17171c', // elevated panel/dialog
        'surface-3': '#1e1e24', // card / input
        'surface-hover': '#25252c', // interactive hover
        'surface-active': '#2d2d36', // pressed / selected

        /* 画布最深处 —— 图片/画布类视图专用，比 background 再暗一档以突出画面本体 */
        canvas: '#0a0a0d',

        /* ── One-pixel Borders ── */
        border: 'rgba(255,255,255,0.07)',
        'border-md': 'rgba(255,255,255,0.12)',
        'border-strong': 'rgba(255,255,255,0.20)',

        /* ── Accent – Indigo (primary action) ── */
        primary: '#5b5fef',
        'primary-hover': '#4e52d6',
        'primary-muted': 'rgba(91,95,239,0.15)',
        'primary-border': 'rgba(91,95,239,0.35)',
        /* 用于「主色底上的主色文字」（选中态 / chip / badge）。
           #5b5fef 在 bg-primary-muted 上仅 2.95:1，AA 正文与 UI 阈值双失败；
           这个亮一档的变体在同一底色上是 7.18:1。 */
        'primary-soft': '#a5b4fc',

        /* ── Accent – Cyan (secondary / info) ── */
        accent: '#06b6d4',
        'accent-hover': '#0891b2',
        'accent-muted': 'rgba(6,182,212,0.12)',
        'accent-border': 'rgba(6,182,212,0.30)',

        /* ── Semantic Status Palette ── */
        ok: '#22c55e',
        'ok-muted': 'rgba(34,197,94,0.12)',
        'ok-border': 'rgba(34,197,94,0.30)',
        warn: '#f59e0b',
        'warn-muted': 'rgba(245,158,11,0.12)',
        'warn-border': 'rgba(245,158,11,0.30)',
        err: '#ef4444',
        'err-muted': 'rgba(239,68,68,0.12)',
        'err-border': 'rgba(239,68,68,0.30)',
        /* 同上：#ef4444 在 err 系浅底上只有 3.89–4.45:1，这个变体升到 5.30:1。 */
        'err-soft': '#f87171',
        info: '#818cf8',
        'info-muted': 'rgba(129,140,248,0.12)',
        'info-border': 'rgba(129,140,248,0.30)',

        /* ── Legacy compat ── */
        cyan: { neon: '#06b6d4', glow: 'rgba(6,182,212,0.20)' },
        emerald: { neon: '#22c55e', glow: 'rgba(34,197,94,0.20)' },
      },

      fontFamily: {
        sans: [
          'Inter',
          '-apple-system',
          'BlinkMacSystemFont',
          '"Segoe UI"',
          'Roboto',
          '"PingFang SC"',
          '"Hiragino Sans GB"',
          '"Microsoft YaHei"',
          '"WenQuanYi Micro Hei"',
          'sans-serif',
        ],
        mono: ['JetBrains Mono', 'Menlo', 'Consolas', 'monospace'],
      },

      fontSize: {
        '2xs': ['11px', { lineHeight: '16px' }],
      },

      borderRadius: {
        sm: '4px',
        DEFAULT: '6px',
        md: '8px',
        lg: '10px',
        xl: '12px',
        '2xl': '16px',
        '3xl': '20px',
      },

      /* 触摸目标 —— 移动端可达性下限（WCAG 2.5.8 为 24px，工作台按 44px 较舒适值） */
      minHeight: {
        touch: '44px',
        'touch-sm': '40px',
      },
      minWidth: {
        touch: '44px',
        'touch-sm': '40px',
      },

      boxShadow: {
        panel: '0 1px 3px rgba(0,0,0,0.5), inset 0 1px 0 0 rgba(255,255,255,0.08), 0 0 0 1px rgba(255,255,255,0.08)',
        card: '0 2px 10px rgba(0,0,0,0.6), inset 0 1px 0 0 rgba(255,255,255,0.08), 0 0 0 1px rgba(255,255,255,0.08)',
        elevated:
          '0 8px 30px rgba(0,0,0,0.75), inset 0 1px 0 0 rgba(255,255,255,0.12), 0 0 0 1px rgba(255,255,255,0.10)',
        /* 键名禁止与 colors 的键同名：颜色插件会为每个同名颜色键额外输出一条 .shadow-<key>，
           后输出的那条把 --tw-shadow 重写为 var(--tw-shadow-colored)，从而劫持这里定义的
           几何与透明度。（历史 bug：键名为 primary/cyan 时，.shadow-primary 实际渲染成
           100% 不透明的 #5b5fef，而非下面的 0.35 rgba。） */
        'glow-primary': '0 2px 16px rgba(91,95,239,0.35)',
        'glow-accent': '0 2px 16px rgba(6,182,212,0.30)',
        inset: 'inset 0 1px 2px rgba(0,0,0,0.5), inset 0 0 0 1px rgba(255,255,255,0.04)',
      },

      animation: {
        'fade-in': 'fadeIn 150ms ease-out',
        'slide-up': 'slideUp 200ms ease-out',
        'spin-slow': 'spin 2s linear infinite',
        'pulse-dot': 'pulseDot 2s cubic-bezier(0.4,0,0.6,1) infinite',
        shimmer: 'shimmer 1.5s infinite',
      },

      keyframes: {
        fadeIn: { from: { opacity: '0' }, to: { opacity: '1' } },
        slideUp: {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        pulseDot: {
          '0%,100%': { opacity: '0.6', transform: 'scale(1)' },
          '50%': { opacity: '1', transform: 'scale(1.2)' },
        },
        shimmer: { '100%': { transform: 'translateX(100%)' } },
      },
    },
  },
  plugins: [],
} satisfies Config;
