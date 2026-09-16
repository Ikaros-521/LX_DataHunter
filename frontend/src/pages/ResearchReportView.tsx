import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Button, Card, Progress, Space, Spin, Tag, Typography, message } from 'antd';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './report.css';
import { fetchReportMarkdown, useResearchDetail } from '../api/queries';

const { Title, Text } = Typography;

const ACTIVE = ['pending', 'collecting', 'crawling_comments', 'analyzing'];

export default function ResearchReportView() {
  const { id } = useParams<{ id: string }>();
  const researchId = id ? Number(id) : null;
  const navigate = useNavigate();
  const { data: task, isLoading: taskLoading } = useResearchDetail(researchId);
  const [md, setMd] = useState<string>('');
  const [mdLoading, setMdLoading] = useState(false);

  const completed = task?.status === 'completed';
  const failed = task?.status === 'failed';

  useEffect(() => {
    if (!researchId || !completed) return;
    let cancelled = false;
    setMdLoading(true);
    fetchReportMarkdown(researchId)
      .then(text => { if (!cancelled) setMd(text); })
      .catch(() => message.error('报告加载失败'))
      .finally(() => { if (!cancelled) setMdLoading(false); });
    return () => { cancelled = true; };
  }, [researchId, completed]);

  if (taskLoading || !task) {
    return <Spin size="large" style={{ display: 'block', marginTop: 80 }} />;
  }

  return (
    <div>
      <Space style={{ marginBottom: 16, width: '100%', justifyContent: 'space-between' }}>
        <Space>
          <Button onClick={() => navigate('/research')}>返回</Button>
          <Title level={4} style={{ margin: 0 }}>{task.title}</Title>
          {completed && <Tag color="success">已完成</Tag>}
          {failed && <Tag color="error">失败：{task.error_message?.slice(0, 80)}</Tag>}
          {ACTIVE.includes(task.status) && (
            <Tag color="processing">{task.stage || task.status}</Tag>
          )}
        </Space>
        {completed && (
          <Button href={`/api/v1/research/${task.id}/file?download=true`} target="_blank">
            下载 .md
          </Button>
        )}
      </Space>

      {ACTIVE.includes(task.status) && (
        <Card style={{ marginBottom: 16 }}>
          <Text type="secondary">调研进行中，页面会自动刷新</Text>
          <Progress percent={Math.round(task.progress)} status="active"
            strokeColor={{ from: '#108ee9', to: '#87d068' }} />
        </Card>
      )}

      {completed && (
        <Card loading={mdLoading}>
          <div className="report-markdown" style={{ maxWidth: 980 }}>
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                img: ({ src, alt, ...props }: any) => {
                  let s = String(src || '');
                  // 报告内截图为相对路径 assets/xxx.png → 改写为后端资源接口
                  if (s.startsWith('assets/')) {
                    s = `/api/v1/research/${task.id}/assets/${encodeURIComponent(s.slice('assets/'.length))}`;
                  }
                  return <img src={s} alt={alt || ''}
                    style={{ maxWidth: '100%', borderRadius: 8, margin: '8px 0' }}
                    loading="lazy" {...props} />;
                },
                a: ({ href, children, ...props }: any) => (
                  <a href={href} target="_blank" rel="noreferrer" {...props}>{children}</a>
                ),
                table: ({ children }: any) => (
                  <div style={{ overflowX: 'auto' }}>
                    <table>{children}</table>
                  </div>
                ),
              }}>
              {md}
            </ReactMarkdown>
          </div>
        </Card>
      )}
    </div>
  );
}
