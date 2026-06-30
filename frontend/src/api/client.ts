import axios from 'axios';
import { notification } from 'antd';

const client = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
});

// ---- 响应拦截器：统一错误提示 ----
client.interceptors.response.use(
  (response) => response,
  (error) => {
    const detail = extractErrorMessage(error);
    const status = error.response?.status || 0;

    notification.error({
      message: `请求失败${status ? ` (${status})` : ''}`,
      description: detail,
      duration: status === 409 ? 3 : 5, // 409 冲突短一些，其他长一些
      placement: 'bottomRight',
    });

    return Promise.reject(error);
  },
);

/** 从 axios 错误中提取人类可读的消息 */
function extractErrorMessage(error: any): string {
  // FastAPI 返回的 detail
  if (error.response?.data?.detail) {
    const d = error.response.data.detail;
    if (Array.isArray(d)) {
      return d.map((e: any) => e.msg || JSON.stringify(e)).join('; ');
    }
    return String(d);
  }
  // 网络错误
  if (error.code === 'ERR_NETWORK' || error.message?.includes('Network')) {
    return '无法连接到服务器，请确认后端已启动 (http://localhost:28000)';
  }
  // 超时
  if (error.code === 'ECONNABORTED' || error.message?.includes('timeout')) {
    return '请求超时，请稍后重试';
  }
  // 兜底
  return error.message || '未知错误';
}

export default client;
