import { useState } from 'react';
import { Row, Col, Table, Select, Button, Space, Tag, message, InputNumber } from 'antd';
import { ThunderboltOutlined } from '@ant-design/icons';
import CrawlProgress from '../components/CrawlProgress';
import PlatformTag from '../components/PlatformTag';
import { useCreators, useSessions, useTriggerCrawl } from '../api/queries';
import { useCrawlProgress } from '../hooks/useCrawlProgress';
import dayjs from 'dayjs';

export default function CrawlerConsole() {
  const [selectedCreator, setSelectedCreator] = useState<number | null>(null);
  const { data: creatorsData } = useCreators({ page_size: 200 });
  const { data: sessionsData, isLoading } = useSessions({ page_size: 20 });
  const crawlMutation = useTriggerCrawl();

  // Find running sessions
  const runningSessions = sessionsData?.items.filter(s => s.status === 'running') || [];
  const [watchingSession, setWatchingSession] = useState<number | null>(
    runningSessions[0]?.id || null
  );
  const progress = useCrawlProgress(watchingSession);

  const [maxItems, setMaxItems] = useState(50);

  const handleTrigger = () => {
    if (!selectedCreator) {
      message.warning('请选择要爬取的创作者');
      return;
    }
    crawlMutation.mutate({ creatorId: selectedCreator, max_items: maxItems }, {
      onSuccess: (res: any) => {
        message.success(`爬取已启动 (Session #${res.data.session_id})`);
        setTimeout(() => setWatchingSession(res.data.session_id), 500);
      },
    });
  };

  const sessionColumns = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 60 },
    { title: '创作者', dataIndex: 'creator_nickname', key: 'creator' },
    { title: '平台', dataIndex: 'platform', key: 'platform', render: (p: string) => <PlatformTag platform={p} /> },
    { title: '触发', dataIndex: 'trigger_type', key: 'trigger', render: (t: string) => <Tag>{t === 'manual' ? '手动' : '定时'}</Tag> },
    {
      title: '状态', dataIndex: 'status', key: 'status',
      render: (s: string) => <Tag color={s === 'completed' ? 'green' : s === 'running' ? 'blue' : 'red'}>{s}</Tag>,
    },
    { title: '内容数', dataIndex: 'total_contents', key: 'count' },
    { title: '时间', dataIndex: 'started_at', key: 'time', render: (t: string) => dayjs(t).format('MM-DD HH:mm:ss') },
  ];

  return (
    <div>
      <Row gutter={[16, 16]}>
        <Col xs={24} md={12}>
          <Space>
            <Select
              showSearch
              placeholder="选择创作者"
              style={{ width: 200 }}
              value={selectedCreator}
              onChange={setSelectedCreator}
              filterOption={(input, option) => (option?.label as string || '').toLowerCase().includes(input.toLowerCase())}
              options={(creatorsData?.items || []).map(c => ({
                label: `${c.nickname || c.platform_uid} (${c.platform})`,
                value: c.id,
              }))}
            />
            <InputNumber min={1} max={500} value={maxItems} onChange={v => setMaxItems(v || 50)}
              addonAfter="条" style={{ width: 120 }} />
            <Button type="primary" icon={<ThunderboltOutlined />}
              loading={crawlMutation.isPending}
              onClick={handleTrigger}>
              立即爬取
            </Button>
          </Space>
        </Col>
      </Row>

      {runningSessions.length > 0 && (
        <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
          <Col span={24}>
            <h3>正在运行的任务</h3>
          </Col>
          {runningSessions.map(s => (
            <Col xs={24} sm={12} md={8} key={s.id}>
              <CrawlProgress
                sessionId={s.id}
                current={progress?.current || 0}
                total={progress?.total || s.total_contents}
                status={progress?.status || s.status}
                creatorName={s.creator_nickname}
              />
            </Col>
          ))}
        </Row>
      )}

      <Row style={{ marginTop: 24 }}>
        <Col span={24}>
          <Table
            title={() => '爬取历史'}
            dataSource={sessionsData?.items || []}
            columns={sessionColumns}
            rowKey="id"
            loading={isLoading}
            size="small"
            pagination={{ pageSize: 20 }}
          />
        </Col>
      </Row>
    </div>
  );
}
