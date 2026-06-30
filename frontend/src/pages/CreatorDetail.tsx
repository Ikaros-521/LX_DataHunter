import { useState, useMemo } from 'react';
import { useParams } from 'react-router-dom';
import { Descriptions, Space, Button, Drawer, Tag, Row, Col, message, Popconfirm, Select } from 'antd';
import { ThunderboltOutlined, ClearOutlined, DownloadOutlined } from '@ant-design/icons';
import PlatformTag from '../components/PlatformTag';
import TrendChart from '../components/TrendChart';
import ContentTable from '../components/ContentTable';
import { useCreator, useSessions, useSessionContents, useTriggerCrawl, useClearCreatorSessions, useBatchDownload } from '../api/queries';
import dayjs from 'dayjs';
import type { Content } from '../api/queries';

/** 判断创作者是否为搜索类虚拟创作者 */
function isSearchCreator(platformUid?: string): boolean {
  return !!platformUid?.startsWith('__hot__') && !!platformUid?.includes('_search');
}

export default function CreatorDetail() {
  const { id } = useParams<{ id: string }>();
  const creatorId = parseInt(id!);
  const { data: creator, isLoading } = useCreator(creatorId);
  const crawlMutation = useTriggerCrawl();
  const clearSessionsMut = useClearCreatorSessions();
  const batchDownloadMut = useBatchDownload();
  const [selectedContentKeys, setSelectedContentKeys] = useState<number[]>([]);

  // 关键词筛选状态
  const [filterKeyword, setFilterKeyword] = useState<string>('');
  const isSearch = isSearchCreator(creator?.platform_uid);

  // 全量请求：仅用于提取关键词下拉选项
  const { data: allSessionsData } = useSessions({ creator_id: creatorId, page_size: 100 });
  // 按关键词筛选的请求：显示给用户的数据
  const keywordParams = useMemo(() => {
    const params: Record<string, any> = { creator_id: creatorId, page_size: 100 };
    if (isSearch && filterKeyword) params.keyword = filterKeyword;
    return params;
  }, [creatorId, isSearch, filterKeyword]);
  const { data: filteredSessionsData } = useSessions(keywordParams);

  const [selectedSessionId, setSelectedSessionId] = useState<number | null>(null);
  const [contentPage, setContentPage] = useState(1);
  const { data: contentsData, isLoading: contentsLoading } = useSessionContents(
    selectedSessionId!,
    { page: contentPage, page_size: 20, sort: 'likes', order: 'desc' }
  );
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedContent, setSelectedContent] = useState<Content | null>(null);

  // 从全量数据提取所有不重复的关键词
  const allSessionsList = allSessionsData?.items || [];

  const keywords = useMemo(() => {
    if (!isSearch) return [];
    const set = new Set<string>();
    allSessionsList.forEach(s => { if (s.keyword) set.add(s.keyword); });
    return Array.from(set).sort();
  }, [allSessionsList, isSearch]);

  // 筛选后的 sessions 用于展示
  const sessions = filteredSessionsData?.items || [];

  // Build trend data from filtered completed sessions
  const trendData = sessions
    .filter(s => s.status === 'completed')
    .reverse()
    .map(s => ({
      time: dayjs(s.finished_at).format('MM-DD HH:mm'),
      likes: s.engagement_total?.likes || 0,
      comments: s.engagement_total?.comments || 0,
      shares: s.engagement_total?.shares || 0,
      plays: s.engagement_total?.plays || 0,
    }));

  return (
    <div>
      <Descriptions
        title={
          <Space>
            {creator?.nickname || '加载中...'}
            {creator?.platform && <PlatformTag platform={creator.platform} />}
          </Space>
        }
        bordered
        column={{ xs: 1, sm: 2 }}
        extra={
          <Button type="primary" icon={<ThunderboltOutlined />}
            loading={crawlMutation.isPending}
            onClick={() => crawlMutation.mutate({ creatorId }, {
              onSuccess: (res: any) => message.success(`爬取已启动 (Session #${res.data.session_id})`),
            })}>
            立即爬取
          </Button>
        }
      >
        <Descriptions.Item label="平台UID">{creator?.platform_uid}</Descriptions.Item>
        <Descriptions.Item label="粉丝">{creator?.follower_count?.toLocaleString()}</Descriptions.Item>
        <Descriptions.Item label="简介" span={2}>{creator?.signature || '-'}</Descriptions.Item>
        <Descriptions.Item label="加入时间">{creator?.created_at ? dayjs(creator.created_at).format('YYYY-MM-DD HH:mm') : '-'}</Descriptions.Item>
        <Descriptions.Item label="最近爬取">{creator?.latest_stats?.fetched_at ? dayjs(creator.latest_stats.fetched_at).format('YYYY-MM-DD HH:mm') : '未爬取'}</Descriptions.Item>
      </Descriptions>

      <Row gutter={[16, 16]} style={{ marginTop: 24 }}>
        <Col span={24}>
          <TrendChart data={trendData} loading={isLoading} />
        </Col>
      </Row>

      <div style={{ marginTop: 24 }}>
        <Space style={{ marginBottom: 16 }}>
          <h3 style={{ margin: 0 }}>爬取历史</h3>
          {isSearch && keywords.length > 0 && (
            <Select
              allowClear
              placeholder="筛选关键词"
              style={{ width: 160 }}
              value={filterKeyword || undefined}
              onChange={(v) => { setFilterKeyword(v || ''); setSelectedSessionId(null); }}
              options={keywords.map(k => ({ label: k, value: k }))}
            />
          )}
          {sessions.length > 0 && (
            <Popconfirm
              title="确认清除该创作者的所有爬取数据?"
              description="将删除所有爬取记录和内容数据，此操作不可恢复"
              onConfirm={() => clearSessionsMut.mutate(creatorId, {
                onSuccess: (res: any) => message.success(res.data.message),
              })}
            >
              <Button size="small" danger icon={<ClearOutlined />}>清除爬取数据</Button>
            </Popconfirm>
          )}
        </Space>
        <Space wrap style={{ marginBottom: 16 }}>
          {sessions.map(s => (
            <Button
              key={s.id}
              type={selectedSessionId === s.id ? 'primary' : 'default'}
              size="small"
              onClick={() => { setSelectedSessionId(s.id); setContentPage(1); }}
            >
              {dayjs(s.finished_at || s.started_at).format('MM-DD HH:mm')}
              <Tag color={s.status === 'completed' ? 'green' : 'blue'} style={{ marginLeft: 8 }}>
                {s.total_contents}条
              </Tag>
            </Button>
          ))}
        </Space>

        {selectedSessionId && (
          <>
            {selectedContentKeys.length > 0 && (
              <Space style={{ marginBottom: 12 }}>
                <Popconfirm
                  title={`确认下载选中的 ${selectedContentKeys.length} 个视频?`}
                  description="仅视频类型内容会被加入下载队列"
                  onConfirm={() => {
                    const videoIds = (contentsData?.items || [])
                      .filter((item) => item.content_type === 'video')
                      .filter((item) => selectedContentKeys.includes(item.id))
                      .map((item) => item.id);
                    if (videoIds.length) {
                      batchDownloadMut.mutate(videoIds, {
                        onSuccess: (res: any) => {
                          message.success(res.data?.message || '下载任务已创建');
                          setSelectedContentKeys([]);
                        },
                      });
                    } else {
                      message.warning('选中的内容中没有视频类型');
                    }
                  }}
                >
                  <Button type="primary" size="small" icon={<DownloadOutlined />}>
                    批量下载 {selectedContentKeys.length} 条
                  </Button>
                </Popconfirm>
              </Space>
            )}
            <ContentTable
              data={contentsData?.items || []}
              loading={contentsLoading}
              total={contentsData?.total}
              page={contentPage}
              onPageChange={(p) => { setContentPage(p); setSelectedContentKeys([]); }}
              onRowClick={(content) => { setSelectedContent(content); setDrawerOpen(true); }}
              rowSelection={{
                selectedRowKeys: selectedContentKeys,
                onChange: (keys) => setSelectedContentKeys(keys as number[]),
              }}
            />
          </>
        )}
      </div>

      <Drawer
        title={selectedContent?.title?.slice(0, 50)}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={480}
      >
        {selectedContent && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="点赞">{selectedContent.likes.toLocaleString()}</Descriptions.Item>
            <Descriptions.Item label="评论">{selectedContent.comments.toLocaleString()}</Descriptions.Item>
            <Descriptions.Item label="转发">{selectedContent.shares.toLocaleString()}</Descriptions.Item>
            <Descriptions.Item label="播放">{selectedContent.plays.toLocaleString()}</Descriptions.Item>
            <Descriptions.Item label="收藏">{selectedContent.collects.toLocaleString()}</Descriptions.Item>
            <Descriptions.Item label="时长">{selectedContent.duration_sec}秒</Descriptions.Item>
            <Descriptions.Item label="发布时间">
              {selectedContent.create_time ? dayjs(selectedContent.create_time).format('YYYY-MM-DD HH:mm') : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="标签">
              {selectedContent.tags?.map(t => <Tag key={t}>{t}</Tag>)}
            </Descriptions.Item>
            {selectedContent.music_title && (
              <Descriptions.Item label="音乐">{selectedContent.music_title}</Descriptions.Item>
            )}
            {selectedContent.url && (
              <Descriptions.Item label="链接">
                <a href={selectedContent.url} target="_blank" rel="noreferrer">打开</a>
              </Descriptions.Item>
            )}
          </Descriptions>
        )}
      </Drawer>
    </div>
  );
}
