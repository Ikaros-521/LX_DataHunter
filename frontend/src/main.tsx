import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider, QueryCache } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { ConfigProvider, notification } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import App from './App';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, retry: 1 },
    mutations: { retry: 0 },
  },
  queryCache: new QueryCache({
    onError: (error: any, _query) => {
      // 查询错误统一提示（mutation 错误由 axios 拦截器处理）
      const detail = error?.response?.data?.detail || error?.message || '未知错误';
      const status = error?.response?.status || 0;
      notification.error({
        message: `数据加载失败${status ? ` (${status})` : ''}`,
        description: typeof detail === 'string' ? detail : JSON.stringify(detail),
        duration: 4,
        placement: 'bottomRight',
      });
    },
  }),
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ConfigProvider locale={zhCN}>
          <App />
        </ConfigProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
