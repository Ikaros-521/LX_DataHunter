import { Progress, Card, Tag } from 'antd';

interface CrawlProgressProps {
  sessionId: number;
  current: number;
  total: number;
  status: string;
  creatorName: string;
}

export default function CrawlProgress({ sessionId, current, total, status, creatorName }: CrawlProgressProps) {
  const percent = total > 0 ? Math.round((current / total) * 100) : 0;
  const statusColor = status === 'completed' ? 'success' : status === 'failed' ? 'exception' : 'active';

  return (
    <Card size="small" title={`#${sessionId} - ${creatorName}`}
      extra={<Tag color={statusColor === 'success' ? 'green' : statusColor === 'exception' ? 'red' : 'blue'}>{status}</Tag>}>
      <Progress percent={percent} status={statusColor === 'active' ? 'active' : statusColor === 'success' ? 'success' : 'exception'} />
      <div style={{ marginTop: 8, color: '#888' }}>
        已捕获: {current} / {total > 0 ? total : '?'}
      </div>
    </Card>
  );
}
