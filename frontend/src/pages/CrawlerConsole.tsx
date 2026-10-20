import { useState, useMemo } from 'react';
import { Row, Col, Table, Select, Button, Space, Tag, message, InputNumber, Popconfirm, Input, Card, Checkbox } from 'antd';
import { ThunderboltOutlined, DeleteOutlined, ClearOutlined, SearchOutlined, LinkOutlined } from '@ant-design/icons';
import CrawlProgress from '../components/CrawlProgress';
import PlatformTag from '../components/PlatformTag';
import { useCreators, useSessions, useTriggerCrawl, useDeleteSession, useClearAllSessions, useBatchDeleteSessions, useCrawlSingleVideo, useBatchDownload } from '../api/queries';
import { useCrawlProgress } from '../hooks/useCrawlProgress';
import dayjs from 'dayjs';
import type { Session } from '../api/queries';

const PLATFORM_OPTIONS = [
  { label: '全部平台', value: '' },
  { label: '抖音', value: 'douyin' },
  { label: 'B站', value: 'bilibili' },
  { label: '快手', value: 'kuaishou' },
  { label: '小红书', value: 'xiaohongshu' },
];

const STATUS_OPTIONS = [
  { label: '全部状态', value: '' },
  { label: '已完成', value: 'completed' },
  { label: '失败', value: 'failed' },
  { label: '运行中', value: 'running' },
];

export default function CrawlerConsole() {
  const [selectedCreator, setSelectedCreator] = useState<number | null>(null);
  const { data: creatorsData } = useCreators({ page_size: 200 });
  const crawlMutation = useTriggerCrawl();
  const deleteSessionMut = useDeleteSession();
  const clearAllMut = useClearAllSessions();
  const batchDeleteMut = useBatchDeleteSessions();

  // 筛选状态
  const [filterPlatform, setFilterPlatform] = useState<string>('');
  const [filterStatus, setFilterStatus] = useState<string>('');
  const [filterNickname, setFilterNickname] = useState<string>('');
  const [filterKeyword, setFilterKeyword] = useState<string>('');
  const [page, setPage] = useState(1);
  const pageSize = 50;

  // 构建查询参数
  const queryParams = useMemo(() => {
    const params: Record<string, any> = { page, page_size: pageSize };
    if (filterPlatform) params.platform = filterPlatform;
    if (filterStatus) params.status = filterStatus;
    if (filterNickname) params.search = filterNickname;
    if (filterKeyword) params.keyword = filterKeyword;
    return params;
  }, [filterPlatform, filterStatus, filterNickname, filterKeyword, page]);

  const { data: sessionsData, isLoading } = useSessions(queryParams);

  // 多选
  const [selectedRowKeys, setSelectedRowKeys] = useState<number[]>([]);

  // 筛选变化时重置页码
  const handleFilterChange = (setter: (v: string) => void, value: string) => {
    setter(value);
    setPage(1);
    setSelectedRowKeys([]);
  };

  // Find running sessions
  const runningSessions = sessionsData?.items.filter(s => s.status === 'running') || [];
  const [watchingSession, setWatchingSession] = useState<number | null>(
    runningSessions[0]?.id || null
  );
  const progress = useCrawlProgress(watchingSession);

  const [maxItems, setMaxItems] = useState(50);

  // 单视频链接爬取
  const [videoUrl, setVideoUrl] = useState('');
  const [autoDownload, setAutoDownload] = useState(true);
  const crawlVideoMut = useCrawlSingleVideo();
  const batchDownloadMut = useBatchDownload();

  const handleCrawlVideo = () => {
    const url = videoUrl.trim();
    if (!url) {
      message.warning('请粘贴视频链接');
      return;
    }
    crawlVideoMut.mutate(url, {
      onSuccess: (res) => {
        const c = res.content;
        const base =
          `已入库「${c.title || res.video_id}」` +
          ` · 点赞 ${c.likes.toLocaleString()} / 评论 ${c.comments.toLocaleString()}`;
        const authorNote = res.creator_new
          ? `，作者「${res.creator_nickname}」已自动加入创作者列表`
          : '';
        const done = (suffix: string) => {
          message.success(base + suffix + authorNote, 6);
          setVideoUrl('');
        };
        if (autoDownload) {
          batchDownloadMut.mutate([res.content_snapshot_id], {
            onSuccess: () => done('，已加入下载队列'),
            onError: () => {
              message.warning('已入库，但加入下载队列失败，可在内容检索中手动下载', 6);
              setVideoUrl('');
            },
          });
        } else {
          done('');
        }
      },
    });
  };

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

  const handleBatchDelete = () => {
    batchDeleteMut.mutate(selectedRowKeys, {
      onSuccess: (res: any) => {
        message.success(res.data.message);
        setSelectedRowKeys([]);
      },
    });
  };

  // 前端过滤：后端 search 是按 nickname，如果没有支持 search 参数则前端过滤
  const filteredItems = useMemo(() => {
    let items = sessionsData?.items || [];
    if (filterNickname) {
      const kw = filterNickname.toLowerCase();
      items = items.filter((s: Session) => s.creator_nickname?.toLowerCase().includes(kw));
    }
    return items;
  }, [sessionsData?.items, filterNickname]);

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
    {
      title: '操作', key: 'action', width: 80,
      render: (_: any, record: Session) => (
        record.status === 'running' ? null : (
          <Popconfirm title="确认删除?" onConfirm={() => deleteSessionMut.mutate(record.id, {
            onSuccess: () => message.success('已删除'),
          })}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        )
      ),
    },
  ];

  const rowSelection = {
    selectedRowKeys,
    onChange: (keys: React.Key[]) => setSelectedRowKeys(keys as number[]),
    getCheckboxProps: (record: Session) => ({
      disabled: record.status === 'running',
    }),
  };

  return (
    <div>
      <Row gutter={[16, 16]}>
        <Col span={24}>
          <Card
            size="small"
            title={<Space><LinkOutlined />单视频链接爬取</Space>}
            extra={<span style={{ color: '#999', fontSize: 12 }}>支持 B站 / 抖音视频页链接及分享短链，视频作者会自动登记为创作者</span>}
          >
            <Space.Compact style={{ width: '100%', maxWidth: 760 }}>
              <Input
                placeholder="粘贴视频链接，如 https://www.bilibili.com/video/BVxxx 或 https://v.douyin.com/xxx"
                value={videoUrl}
                onChange={e => setVideoUrl(e.target.value)}
                onPressEnter={handleCrawlVideo}
                allowClear
                disabled={crawlVideoMut.isPending}
              />
              <Button
                type="primary"
                loading={crawlVideoMut.isPending}
                onClick={handleCrawlVideo}
              >
                {crawlVideoMut.isPending ? '抓取中…' : '爬取该视频'}
              </Button>
            </Space.Compact>
            <div style={{ marginTop: 8 }}>
              <Checkbox
                checked={autoDownload}
                onChange={e => setAutoDownload(e.target.checked)}
              >
                爬取后自动加入下载队列
              </Checkbox>
            </div>
          </Card>
        </Col>
      </Row>

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
          {/* 筛选区域 */}
          <Space wrap style={{ marginBottom: 16 }}>
            <Select
              style={{ width: 120 }}
              value={filterPlatform}
              onChange={v => handleFilterChange(setFilterPlatform, v)}
              options={PLATFORM_OPTIONS}
            />
            <Select
              style={{ width: 120 }}
              value={filterStatus}
              onChange={v => handleFilterChange(setFilterStatus, v)}
              options={STATUS_OPTIONS}
            />
            <Input
              placeholder="搜索创作者"
              prefix={<SearchOutlined />}
              style={{ width: 160 }}
              allowClear
              value={filterNickname}
              onChange={e => handleFilterChange(setFilterNickname, e.target.value)}
            />
            <Input
              placeholder="搜索关键词"
              prefix={<SearchOutlined />}
              style={{ width: 160 }}
              allowClear
              value={filterKeyword}
              onChange={e => handleFilterChange(setFilterKeyword, e.target.value)}
            />
            {selectedRowKeys.length > 0 && (
              <Popconfirm
                title={`确认删除选中的 ${selectedRowKeys.length} 条记录?`}
                onConfirm={handleBatchDelete}
              >
                <Button danger icon={<DeleteOutlined />}>
                  批量删除 {selectedRowKeys.length} 条
                </Button>
              </Popconfirm>
            )}
          </Space>

          <Table
            title={() => (
              <Space>
                <span>爬取历史</span>
                {sessionsData && <Tag>共 {sessionsData.total} 条</Tag>}
                <Popconfirm
                  title="确认清除所有爬取数据?"
                  description="将删除所有非运行中的爬取记录和内容数据，此操作不可恢复"
                  onConfirm={() => clearAllMut.mutate(undefined, {
                    onSuccess: (res: any) => message.success(res.data.message),
                  })}
                >
                  <Button size="small" danger icon={<ClearOutlined />}>清除所有数据</Button>
                </Popconfirm>
              </Space>
            )}
            dataSource={filteredItems}
            columns={sessionColumns}
            rowKey="id"
            rowSelection={rowSelection}
            loading={isLoading}
            size="small"
            pagination={{
              current: page,
              pageSize,
              total: sessionsData?.total || 0,
              onChange: (p: number) => { setPage(p); setSelectedRowKeys([]); },
              showTotal: (total: number) => `第 ${page} / ${Math.ceil(total / pageSize)} 页`,
              showSizeChanger: false,
            }}
          />
        </Col>
      </Row>
    </div>
  );
}
