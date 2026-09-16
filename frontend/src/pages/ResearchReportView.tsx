import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Button, Card, Modal, Progress, Select, Space, Spin, Table, Tag, Tooltip,
  Typography, message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './report.css';
import {
  fetchReportMarkdown, useResearchDetail, useResearchDanmakus,
} from '../api/queries';
import type { ResearchDanmakuItem } from '../api/queries';

const { Title, Text } = Typography;

const ACTIVE = ['pending', 'collecting', 'crawling_comments', 'analyzing'];

const DANMAKU_MODE: Record<number, string> = { 1: '滚动', 4: '底部', 5: '顶部' };

function fmtDmTime(t: number): string {
  return `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
}

const DANMAKU_PAGE_SIZE = 50;

function DanmakuModal({ open, onClose, researchId }: {
  open: boolean; onClose: () => void; researchId: number;
}) {
  const [contentId, setContentId] = useState<string | undefined>();
  const [page, setPage] = useState(1);
  const { data, isLoading } = useResearchDanmakus(
    researchId, { content_id: contentId, page, page_size: DANMAKU_PAGE_SIZE }, open);

  const columns: ColumnsType<ResearchDanmakuItem> = [
    { title: '视频内', dataIndex: 'dm_time', width: 80,
      render: (v: number) => <Tag>{fmtDmTime(v)}</Tag> },
    { title: '弹幕内容', dataIndex: 'text', ellipsis: { showTitle: false },
      render: (v: string) => <Tooltip title={v} placement="topLeft">{v}</Tooltip> },
    { title: '发送时间', dataIndex: 'send_time', width: 150,
      render: (v: string | null) => v ? new Date(v).toLocaleString('zh-CN') : '-' },
    { title: '模式', dataIndex: 'mode', width: 70,
      render: (v: number) => DANMAKU_MODE[v] || v },
    { title: '用户哈希', dataIndex: 'user_hash', width: 110, ellipsis: true },
  ];

  return (
    <Modal title={`弹幕原始数据（共 ${data?.total ?? 0} 条）`} open={open} onCancel={onClose}
      width={860} footer={null} destroyOnClose>
      <Space style={{ marginBottom: 12, width: '100%', justifyContent: 'space-between' }}>
        <Select style={{ minWidth: 320 }} allowClear placeholder="全部视频"
          value={contentId}
          onChange={v => { setContentId(v); setPage(1); }}
          options={(data?.contents || []).map(c => ({
            value: c.content_id,
            label: `${c.title || `视频 ${c.content_id}`}（${c.count} 条）`,
          }))} />
        <Button href={`/api/v1/research/${researchId}/danmakus/export`} target="_blank">
          导出 CSV
        </Button>
      </Space>
      <Table<ResearchDanmakuItem>
        rowKey="id" size="small" loading={isLoading}
        dataSource={data?.items || []} columns={columns}
        pagination={{
          current: page, pageSize: DANMAKU_PAGE_SIZE, total: data?.total || 0,
          onChange: p => setPage(p), showSizeChanger: false,
          showTotal: t => `共 ${t} 条`,
        }}
      />
      {!isLoading && !data?.total && (
        <Text type="secondary">无弹幕数据（该任务未开启弹幕存库，或B站视频无弹幕）</Text>
      )}
    </Modal>
  );
}

export default function ResearchReportView() {
  const { id } = useParams<{ id: string }>();
  const researchId = id ? Number(id) : null;
  const navigate = useNavigate();
  const { data: task, isLoading: taskLoading } = useResearchDetail(researchId);
  const [md, setMd] = useState<string>('');
  const [mdLoading, setMdLoading] = useState(false);
  const [danmakuOpen, setDanmakuOpen] = useState(false);

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
          <Space>
            <Button onClick={() => setDanmakuOpen(true)}>弹幕数据</Button>
            <Button href={`/api/v1/research/${task.id}/file?download=true`} target="_blank">
              下载 .md
            </Button>
          </Space>
        )}
      </Space>

      {completed && researchId != null && (
        <DanmakuModal open={danmakuOpen} onClose={() => setDanmakuOpen(false)}
          researchId={researchId} />
      )}

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
