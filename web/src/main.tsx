import React from 'react';
import { MotionConfig } from 'framer-motion';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import App from './App';
import { ErrorBoundary } from '@/components/ui/ErrorBoundary';
import { ToastProvider } from '@/components/ui/ToastProvider';
import './index.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary>
        <ToastProvider>
          <MotionConfig reducedMotion="user">
            <App />
          </MotionConfig>
        </ToastProvider>
      </ErrorBoundary>
    </QueryClientProvider>
  </React.StrictMode>,
);
