import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { Descriptions, Space, Button, Drawer, Tag, Row, Col, message } from 'antd';
import { ThunderboltOutlined } from '@ant-design/icons';
import PlatformTag from '../components/PlatformTag';
import TrendChart from '../components/TrendChart';
import ContentTable from '../components/ContentTable';
import { useCreator, useSessions, useSessionContents, useTriggerCrawl } from '../api/queries';
import dayjs from 'dayjs';
import type { Content } from '../api/queries';

export default function CreatorDetail() {
  const { id } = useParams<{ id: string }>();
  const creatorId = parseInt(id!);
  const { data: creator, isLoading } = useCreator(creatorId);
  const { data: sessionsData } = useSessions({ creator_id: creatorId, page_size: 100 });
  const crawlMutation = useTriggerCrawl();

  const [selectedSessionId, setSelectedSessionId] = useState<number | null>(null);
  const [contentPage, setContentPage] = useState(1);
  const { data: contentsData, isLoading: contentsLoading } = useSessionContents(
    selectedSessionId!,
    { page: contentPage, page_size: 20, sort: 'likes', order: 'desc' }
  );
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedContent, setSelectedContent] = useState<Content | null>(null);

  const sessions = sessionsData?.items || [];

  // Build trend data from all completed sessions
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
        loading={isLoading}
        extra={
          <Button type="primary" icon={<ThunderboltOutlined />}
            loading={crawlMutation.isPending}
            onClick={() => crawlMutation.mutate(creatorId, {
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
        <h3>爬取历史</h3>
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
          <ContentTable
            data={contentsData?.items || []}
            loading={contentsLoading}
            total={contentsData?.total}
            page={contentPage}
            onPageChange={setContentPage}
            onRowClick={(content) => { setSelectedContent(content); setDrawerOpen(true); }}
          />
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
