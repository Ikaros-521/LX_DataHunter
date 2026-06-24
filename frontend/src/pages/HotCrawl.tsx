import { useState } from 'react';
import { Card, Row, Col, Select, Input, InputNumber, Button, message, Spin, Tag } from 'antd';
import { FireOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import client from '../api/client';
import { useCrawlProgress } from '../hooks/useCrawlProgress';

interface HotCategory {
  platform: string; platform_name?: string; id: string; name: string; desc: string;
}

export default function HotCrawl() {
  const qc = useQueryClient();
  const [platform, setPlatform] = useState('bilibili');
  const [category, setCategory] = useState('');
  const [maxItems, setMaxItems] = useState(50);
  const [keyword, setKeyword] = useState('');
  const [watchingSession, setWatchingSession] = useState<number | null>(null);
  const progress = useCrawlProgress(watchingSession);

  const { data: categories, isLoading } = useQuery<HotCategory[]>({
    queryKey: ['hot-categories'],
    queryFn: () => client.get('/hot/categories').then(r => r.data),
  });

  const crawlMut = useMutation({
    mutationFn: (body: any) => client.post('/hot/crawl', body),
    onSuccess: (res: any) => {
      message.success(`热门爬取已启动 (Session #${res.data.session_id})`);
      setWatchingSession(res.data.session_id);
      qc.invalidateQueries({ queryKey: ['sessions'] });
    },
  });

  const platformCategories = (categories || []).filter(c => c.platform === platform);

  const handleCrawl = () => {
    if (!category) { message.warning('请选择分类'); return; }
    if (category === 'search' && !keyword) { message.warning('请输入搜索关键词'); return; }
    crawlMut.mutate({ platform, category, max_items: maxItems, keyword });
  };

  return (
    <div>
      <h3><FireOutlined /> 热门内容爬取</h3>
      <div style={{ color: '#888', marginBottom: 16, fontSize: 13 }}>
        爬取平台热门/排行榜内容，无需指定创作者。结果存入数据库后可检索分析。
      </div>

      <Card style={{ maxWidth: 600, marginBottom: 24 }}>
        <Row gutter={[16, 16]}>
          <Col span={24}>
            <div style={{ marginBottom: 4, fontWeight: 500 }}>平台</div>
            <Select value={platform} onChange={v => { setPlatform(v); setCategory(''); }}
              style={{ width: '100%' }}
              options={[
                { label: 'B站', value: 'bilibili' },
                { label: '抖音', value: 'douyin' },
              ]} />
          </Col>
          <Col span={24}>
            <div style={{ marginBottom: 4, fontWeight: 500 }}>热门分类</div>
            {isLoading ? <Spin /> : (
              <Select value={category} onChange={setCategory} style={{ width: '100%' }}
                placeholder="选择分类" loading={isLoading}
                options={platformCategories.map(c => ({
                  label: `${c.name} — ${c.desc}`,
                  value: c.id,
                }))} />
            )}
          </Col>
          {category === 'search' && (
            <Col span={24}>
              <div style={{ marginBottom: 4, fontWeight: 500 }}>搜索关键词</div>
              <Input value={keyword} onChange={e => setKeyword(e.target.value)}
                placeholder="输入搜索关键词" />
            </Col>
          )}
          <Col span={12}>
            <div style={{ marginBottom: 4, fontWeight: 500 }}>爬取数量</div>
            <InputNumber min={1} max={200} value={maxItems} onChange={v => setMaxItems(v || 50)}
              addonAfter="条" style={{ width: '100%' }} />
          </Col>
          <Col span={12} style={{ display: 'flex', alignItems: 'flex-end' }}>
            <Button type="primary" icon={<ThunderboltOutlined />} loading={crawlMut.isPending}
              onClick={handleCrawl} block size="large">
              开始爬取
            </Button>
          </Col>
        </Row>
      </Card>

      {watchingSession && progress && (
        <Card title={`Session #${watchingSession}`} size="small" style={{ maxWidth: 600 }}>
          <div>已抓取: {progress.current} / {progress.total || maxItems}</div>
          <Tag color={progress.status === 'completed' ? 'green' : progress.status === 'running' ? 'blue' : 'red'}>
            {progress.status}
          </Tag>
        </Card>
      )}
    </div>
  );
}
