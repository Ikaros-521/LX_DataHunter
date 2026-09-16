import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Badge, Button, Card, DatePicker, Form, Input, InputNumber, Modal, Popconfirm,
  Progress, Radio, Select, Space, Switch, Table, Tabs, Tag, Tooltip, message,
} from 'antd';
import type { Dayjs } from 'dayjs';
import type { ColumnsType } from 'antd/es/table';
import {
  useAddCompetitor, useCompetitors, useDeleteCompetitor,
  useDeleteResearch, useDiscoverCompetitors, useResearchList,
  useResearchSchedule, useUpdateCompetitor, useUpdateResearchSchedule,
  useCreateResearch, useResearchKeywords, useAddResearchKeyword,
  useUpdateResearchKeyword, useDeleteResearchKeyword,
} from '../api/queries';
import type { Competitor, DiscoveredCompetitor, ResearchKeyword } from '../api/queries';

const PLATFORM_OPTIONS = [
  { value: 'bilibili', label: 'B站' },
  { value: 'douyin', label: '抖音' },
];

const PLATFORM_LABEL: Record<string, string> = { bilibili: 'B站', douyin: '抖音' };

const STATUS_META: Record<string, { color: string; text: string }> = {
  pending: { color: 'default', text: '排队中' },
  collecting: { color: 'processing', text: '抓取数据' },
  crawling_comments: { color: 'processing', text: '爬取评论' },
  analyzing: { color: 'processing', text: 'LLM 分析' },
  completed: { color: 'success', text: '已完成' },
  failed: { color: 'error', text: '失败' },
};

function fmtNum(n?: number): string {
  if (!n) return '0';
  if (Math.abs(n) >= 10000) return `${(n / 10000).toFixed(1)}万`;
  return String(n);
}

// ---------- 竞品管理 Tab ----------

function AddCompetitorModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [form] = Form.useForm();
  const add = useAddCompetitor();

  const onOk = async () => {
    const v = await form.validateFields();
    await add.mutateAsync({
      platform: v.platform,
      platform_uid: v.platform_uid.trim(),
      name: v.name?.trim() || '',
    });
    message.success('竞品已添加，可在「调研报告」页发起调研');
    form.resetFields();
    onClose();
  };

  return (
    <Modal title="添加竞品" open={open} onOk={onOk}
      confirmLoading={add.isPending} onCancel={onClose} destroyOnClose>
      <Form form={form} layout="vertical" initialValues={{ platform: 'bilibili' }}>
        <Form.Item name="platform" label="平台" rules={[{ required: true }]}>
          <Select options={PLATFORM_OPTIONS} />
        </Form.Item>
        <Form.Item
          name="platform_uid" label="账号标识"
          extra="B站：粘贴空间链接（space.bilibili.com/xxx）或数字 mid；抖音：粘贴主页链接（douyin.com/user/xxx）或 sec_uid"
          rules={[{ required: true, message: '请输入主页链接或 uid' }]}
        >
          <Input placeholder="https://space.bilibili.com/2267573" allowClear />
        </Form.Item>
        <Form.Item name="name" label="备注名（可选）">
          <Input placeholder="如：竞品A-XX品牌" allowClear maxLength={50} />
        </Form.Item>
      </Form>
    </Modal>
  );
}

function DiscoverModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [platform, setPlatform] = useState('bilibili');
  const [keyword, setKeyword] = useState('');
  const [maxPages, setMaxPages] = useState(3);
  const [candidates, setCandidates] = useState<DiscoveredCompetitor[]>([]);
  const [selected, setSelected] = useState<React.Key[]>([]);
  // 筛选条件（客户端过滤）
  const [minFollowers, setMinFollowers] = useState<number | null>(null);
  const [activeDays, setActiveDays] = useState<number | null>(null);
  const discover = useDiscoverCompetitors();
  const add = useAddCompetitor();

  const onSearch = async () => {
    if (!keyword.trim()) return;
    setCandidates([]);
    setSelected([]);
    try {
      const data = await discover.mutateAsync({
        platform, keyword: keyword.trim(), max_items: 30, max_pages: maxPages,
      });
      setCandidates(data.candidates);
      if (data.message) message.info(data.message);
      if (!data.candidates.length && !data.message) message.warning('未发现候选账号');
    } catch {
      /* 错误已由拦截器提示 */
    }
  };

  // 筛选：最少粉丝 + 近 N 天有发布
  const filtered = candidates.filter(c => {
    if (minFollowers != null && (c.follower_count || 0) < minFollowers) return false;
    if (activeDays != null) {
      if (!c.latest_pub_time) return false;
      const limit = Date.now() - activeDays * 86400_000;
      const ts = new Date(c.latest_pub_time).getTime();
      if (Number.isNaN(ts) || ts < limit) return false;
    }
    return true;
  });

  const onAddSelected = async () => {
    const rows = filtered.filter(c => selected.includes(c.platform_uid));
    if (!rows.length) return;
    let ok = 0;
    for (const r of rows) {
      try {
        await add.mutateAsync({ platform: r.platform, platform_uid: r.homepage_url, name: r.nickname });
        ok++;
      } catch {
        /* 重复等错误由拦截器提示，继续添加其余 */
      }
    }
    if (ok) message.success(`已添加 ${ok} 个竞品`);
    onClose();
    setCandidates([]);
    setSelected([]);
    setKeyword('');
    setMinFollowers(null);
    setActiveDays(null);
  };

  const columns: ColumnsType<DiscoveredCompetitor> = [
    { title: '账号', dataIndex: 'nickname', ellipsis: true,
      sorter: (a, b) => (a.nickname || '').localeCompare(b.nickname || ''),
      render: (v: string, r) => (
        <Tooltip title={r.signature || r.homepage_url}>
          <a href={r.homepage_url} target="_blank" rel="noreferrer">{v || r.platform_uid}</a>
        </Tooltip>
      ) },
    { title: '平台', dataIndex: 'platform', width: 70,
      filters: [
        { text: 'B站', value: 'bilibili' },
        { text: '抖音', value: 'douyin' },
      ],
      onFilter: (value, r) => r.platform === value,
      render: (v: string) => PLATFORM_LABEL[v] || v },
    { title: '粉丝', dataIndex: 'follower_count', width: 100,
      sorter: (a, b) => (a.follower_count || 0) - (b.follower_count || 0),
      defaultSortOrder: 'descend',
      render: (v: number) => fmtNum(v) },
    { title: '搜索命中', dataIndex: 'video_hits', width: 90,
      sorter: (a, b) => a.video_hits - b.video_hits,
      render: (v: number) => <Badge count={v} color="blue" /> },
    { title: '最新发布', dataIndex: 'latest_pub_time', width: 110,
      sorter: (a, b) => (a.latest_pub_time || '').localeCompare(b.latest_pub_time || ''),
      render: (v: string) => v || '-' },
    { title: '命中播放', dataIndex: 'total_plays', width: 90,
      sorter: (a, b) => (a.total_plays || 0) - (b.total_plays || 0),
      render: (v: number) => fmtNum(v) },
    { title: '命中视频标题', dataIndex: 'sample_titles', ellipsis: true,
      render: (v: string[]) => v?.join('；') || '-' },
  ];

  return (
    <Modal title="关键词发现竞品" open={open} onCancel={onClose} width={1000}
      footer={[
        <Button key="cancel" onClick={onClose}>关闭</Button>,
        <Button key="add" type="primary" disabled={!selected.length}
          loading={add.isPending} onClick={onAddSelected}>
          添加所选（{selected.length}）
        </Button>,
      ]}>
      <Space.Compact style={{ width: '100%', marginBottom: 12 }}>
        <Select value={platform} onChange={setPlatform} style={{ width: 100 }} options={PLATFORM_OPTIONS} />
        <Input placeholder="行业/产品关键词，如：桌面收纳" value={keyword}
          onChange={e => setKeyword(e.target.value)}
          onPressEnter={onSearch} allowClear />
        <Select value={maxPages} onChange={setMaxPages} style={{ width: 130 }}
          options={[
            { value: 1, label: '1 页搜索' },
            { value: 3, label: '3 页搜索' },
            { value: 5, label: '5 页搜索' },
            { value: 10, label: '10 页搜索' },
          ]} />
        <Button type="primary" loading={discover.isPending} onClick={onSearch}>
          {discover.isPending ? '搜索中…' : '搜索'}
        </Button>
      </Space.Compact>
      {candidates.length > 0 && (
        <Space style={{ marginBottom: 12 }} size="middle">
          <span style={{ color: '#888', fontSize: 13 }}>
            {candidates.length} 个账号（B站每页约 42 条结果，页数越多覆盖越广）
          </span>
          <InputNumber placeholder="最少粉丝" min={0} style={{ width: 110 }}
            value={minFollowers} onChange={v => setMinFollowers(v)} />
          <Select placeholder="发布时效" allowClear style={{ width: 120 }}
            value={activeDays} onChange={v => setActiveDays(v)}
            options={[
              { value: 7, label: '近 7 天发布' },
              { value: 30, label: '近 30 天发布' },
              { value: 90, label: '近 90 天发布' },
            ]} />
        </Space>
      )}
      <Table<DiscoveredCompetitor>
        rowKey="platform_uid" size="small"
        dataSource={filtered}
        rowSelection={{ selectedRowKeys: selected, onChange: setSelected }}
        pagination={{ pageSize: 10 }}
        columns={columns}
      />
    </Modal>
  );
}

function CompetitorsTab() {
  const { data, isLoading } = useCompetitors();
  const del = useDeleteCompetitor();
  const update = useUpdateCompetitor();

  return (
    <Table<Competitor>
      rowKey="id" size="middle" loading={isLoading}
      dataSource={data?.items || []}
      pagination={{ pageSize: 10 }}
      columns={[
        { title: '备注名', dataIndex: 'name', width: 160, ellipsis: true,
          render: (v: string, r) => v || r.nickname || r.platform_uid },
        { title: '平台', dataIndex: 'platform', width: 80,
          render: (v: string) => <Tag>{PLATFORM_LABEL[v] || v}</Tag> },
        { title: '平台昵称', dataIndex: 'nickname', ellipsis: true,
          render: (v: string, r) => (
            <a href={r.homepage_url} target="_blank" rel="noreferrer">{v || '（待首次调研回填）'}</a>
          ) },
        { title: '粉丝', dataIndex: 'follower_count', width: 90,
          render: (v: number) => fmtNum(v) },
        { title: '最近调研', dataIndex: 'last_researched_at', width: 160,
          render: (v: string | null) => v ? new Date(v).toLocaleString('zh-CN') : '未调研' },
        { title: '启用', dataIndex: 'enabled', width: 70,
          render: (v: boolean, r) => (
            <Switch checked={v} size="small"
              onChange={checked => update.mutate({ id: r.id, enabled: checked })} />
          ) },
        { title: '操作', width: 80,
          render: (_: any, r) => (
            <Popconfirm title="删除该竞品？" onConfirm={() => del.mutate(r.id)}>
              <Button size="small" danger>删除</Button>
            </Popconfirm>
          ) },
      ]}
    />
  );
}

// ---------- 发起调研表单 ----------

function ResearchLauncher() {
  const [form] = Form.useForm();
  const [mode, setMode] = useState<'accounts' | 'keyword'>('accounts');
  const { data: compData } = useCompetitors();
  const { data: kwData } = useResearchKeywords();
  const create = useCreateResearch();

  const compOptions = (compData?.items || [])
    .filter(c => c.enabled)
    .map(c => ({
      value: c.id,
      label: `${c.name || c.nickname || c.platform_uid}（${PLATFORM_LABEL[c.platform] || c.platform}）`,
    }));
  const kwOptions = (kwData?.items || [])
    .filter(k => k.enabled)
    .map(k => ({ value: k.id, label: `${k.keyword}（${PLATFORM_LABEL[k.platform] || k.platform}）` }));

  const onOk = async () => {
    const v = await form.validateFields();
    const range = v.range as [Dayjs, Dayjs] | undefined;
    await create.mutateAsync({
      title: v.title?.trim() || '',
      mode,
      competitor_ids: mode === 'accounts' ? v.targets : [],
      keyword_ids: mode === 'keyword' ? v.targets : [],
      date_from: range?.[0]?.format('YYYY-MM-DD') || null,
      date_to: range?.[1]?.format('YYYY-MM-DD') || null,
      top_videos_for_comments: v.top_videos,
      max_comments_per_video: v.max_comments,
      ...(mode === 'accounts'
        ? { max_videos_per_competitor: v.max_videos }
        : { max_videos_per_keyword: v.max_videos, max_search_pages: v.max_pages }),
    });
    message.success('调研已启动，可在下方列表查看进度');
    form.resetFields(['title', 'targets', 'range']);
  };

  const targetRules = [{ required: true, message: mode === 'accounts' ? '请选择竞品账号' : '请选择关键词' }];

  return (
    <Card title="发起竞品调研" size="small" style={{ marginBottom: 16 }}>
      <Form form={form} layout="inline"
        initialValues={{ max_videos: 8, max_pages: 2, top_videos: 5, max_comments: 50 }}
        style={{ flexWrap: 'wrap', gap: 8 }}>
        <Form.Item>
          <Radio.Group value={mode} onChange={e => setMode(e.target.value)}
            optionType="button" buttonStyle="solid">
            <Radio.Button value="accounts">账号分析</Radio.Button>
            <Radio.Button value="keyword">赛道分析（关键词）</Radio.Button>
          </Radio.Group>
        </Form.Item>
        <Form.Item name="targets" rules={targetRules}>
          {mode === 'accounts' ? (
            <Select mode="multiple" placeholder="选择竞品账号（可多选）"
              options={compOptions} style={{ minWidth: 300 }} allowClear
              maxTagCount="responsive" />
          ) : (
            <Select mode="multiple" placeholder="选择调研关键词（可多选）"
              options={kwOptions} style={{ minWidth: 300 }} allowClear
              maxTagCount="responsive" notFoundContent={
                <span style={{ color: '#888' }}>先在「调研对象」页签添加关键词</span>
              } />
          )}
        </Form.Item>
        <Form.Item name="range">
          <DatePicker.RangePicker placeholder={['发布起', '发布止']} allowEmpty={[true, true]} />
        </Form.Item>
        <Form.Item name="max_videos" label={mode === 'accounts' ? '每账号视频数' : '每关键词视频数'}>
          <InputNumber min={1} max={50} style={{ width: 70 }} />
        </Form.Item>
        {mode === 'keyword' && (
          <Form.Item name="max_pages" label="搜索翻页">
            <InputNumber min={1} max={10} style={{ width: 60 }} />
          </Form.Item>
        )}
        <Form.Item name="top_videos" label="爬评论视频数">
          <InputNumber min={0} max={10} style={{ width: 60 }} />
        </Form.Item>
        <Form.Item name="max_comments" label="每视频评论数">
          <InputNumber min={1} max={500} style={{ width: 70 }} />
        </Form.Item>
        <Form.Item name="title">
          <Input placeholder="报告标题（可选）" style={{ width: 180 }} allowClear maxLength={50} />
        </Form.Item>
        <Form.Item>
          <Button type="primary" loading={create.isPending} onClick={onOk}>
            开始调研
          </Button>
        </Form.Item>
      </Form>
    </Card>
  );
}

// ---------- 定时配置 ----------

function ScheduleCard() {
  const { data: sched } = useResearchSchedule();
  const update = useUpdateResearchSchedule();
  const [form] = Form.useForm();

  if (!sched) return null;

  const onSave = async () => {
    const v = await form.validateFields();
    await update.mutateAsync({
      interval_minutes: v.interval_minutes,
      enabled: v.enabled,
      params: { days: v.days, title_prefix: '定时竞品调研' },
    });
    message.success('定时配置已保存');
  };

  return (
    <Card title="定时自动调研" size="small" style={{ marginBottom: 16 }}>
      <Form form={form} layout="inline"
        initialValues={{
          enabled: sched.enabled,
          interval_minutes: sched.interval_minutes,
          days: sched.params?.days ?? 30,
        }}
        style={{ gap: 8 }}>
        <Form.Item name="enabled" label="启用">
          <Switch />
        </Form.Item>
        <Form.Item name="interval_minutes" label="间隔（分钟）"
          rules={[{ required: true }]}>
          <InputNumber min={30} max={43200} style={{ width: 110 }} />
        </Form.Item>
        <Form.Item name="days" label="回看天数">
          <InputNumber min={1} max={365} style={{ width: 80 }} />
        </Form.Item>
        <Form.Item>
          <Button loading={update.isPending} onClick={onSave}>保存</Button>
        </Form.Item>
        {sched.enabled && sched.next_run_at && (
          <Form.Item>
            <span style={{ color: '#888' }}>
              下次运行：{new Date(sched.next_run_at).toLocaleString('zh-CN')}
            </span>
          </Form.Item>
        )}
      </Form>
    </Card>
  );
}

// ---------- 调研关键词管理 ----------

function KeywordsTab() {
  const { data, isLoading } = useResearchKeywords();
  const add = useAddResearchKeyword();
  const del = useDeleteResearchKeyword();
  const update = useUpdateResearchKeyword();
  const [form] = Form.useForm();

  const onAdd = async () => {
    const v = await form.validateFields();
    await add.mutateAsync({ keyword: v.keyword.trim(), platform: v.platform });
    message.success('关键词已添加');
    form.resetFields(['keyword']);
  };

  const columns: ColumnsType<ResearchKeyword> = [
    { title: '关键词', dataIndex: 'keyword', width: 200,
      render: (v: string) => <Tag color="purple">{v}</Tag> },
    { title: '平台', dataIndex: 'platform', width: 80,
      render: (v: string) => PLATFORM_LABEL[v] || v },
    { title: '最近调研摘要', dataIndex: 'extra_info', ellipsis: true,
      render: (info: Record<string, any>) => {
        if (!info || !info.video_count) return '未调研';
        const authors = (info.top_authors || []).slice(0, 3).join('、');
        return `头部视频 ${info.video_count} 条，平均播放 ${fmtNum(info.avg_plays)}`
          + (authors ? `，玩家：${authors}` : '');
      } },
    { title: '最近调研', dataIndex: 'last_researched_at', width: 160,
      render: (v: string | null) => v ? new Date(v).toLocaleString('zh-CN') : '未调研' },
    { title: '启用', dataIndex: 'enabled', width: 70,
      render: (v: boolean, r) => (
        <Switch checked={v} size="small"
          onChange={checked => update.mutate({ id: r.id, enabled: checked })} />
      ) },
    { title: '操作', width: 80,
      render: (_: any, r) => (
        <Popconfirm title="删除该关键词？" onConfirm={() => del.mutate(r.id)}>
          <Button size="small" danger>删除</Button>
        </Popconfirm>
      ) },
  ];

  return (
    <div>
      <Card size="small" style={{ marginBottom: 12 }}>
        <Form form={form} layout="inline" initialValues={{ platform: 'bilibili' }}
          style={{ gap: 8 }}>
          <Form.Item name="platform">
            <Select options={PLATFORM_OPTIONS} style={{ width: 100 }} />
          </Form.Item>
          <Form.Item name="keyword"
            rules={[{ required: true, message: '请输入关键词' }]}>
            <Input placeholder="赛道/品类关键词，如：AI剪辑、桌面收纳"
              style={{ width: 280 }} allowClear maxLength={50}
              onPressEnter={onAdd} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" loading={add.isPending} onClick={onAdd}>添加关键词</Button>
          </Form.Item>
        </Form>
      </Card>
      <Table<ResearchKeyword> rowKey="id" size="middle" loading={isLoading}
        dataSource={data?.items || []} columns={columns}
        pagination={{ pageSize: 10 }} />
    </div>
  );
}

// ---------- 调研报告 Tab ----------

function ReportsTab() {
  const navigate = useNavigate();
  const { data, isLoading } = useResearchList();
  const del = useDeleteResearch();
  const activeStatuses = ['pending', 'collecting', 'crawling_comments', 'analyzing'];

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60, key: 'id' },
    { title: '标题', dataIndex: 'title', ellipsis: true, key: 'title' },
    { title: '模式', dataIndex: 'mode', width: 90, key: 'mode',
      render: (v: string) => v === 'keyword'
        ? <Tag color="purple">赛道分析</Tag>
        : <Tag color="blue">账号分析</Tag> },
    { title: '对象数', dataIndex: 'competitor_count', width: 80, key: 'competitor_count' },
    { title: '状态', dataIndex: 'status', width: 200, key: 'status',
      render: (s: string, r: any) => {
        const meta = STATUS_META[s] || { color: 'default', text: s };
        const active = activeStatuses.includes(s);
        return (
          <Space direction="vertical" size={2} style={{ width: '100%' }}>
            <Tag color={meta.color}>{meta.text}</Tag>
            {active && <Progress percent={Math.round(r.progress)} size="small" />}
            {!active && r.stage && <span style={{ color: '#888', fontSize: 12 }}>{r.stage}</span>}
          </Space>
        );
      } },
    { title: '触发', dataIndex: 'trigger_type', width: 80, key: 'trigger_type',
      render: (v: string) => (v === 'scheduled' ? '定时' : '手动') },
    { title: '创建时间', dataIndex: 'created_at', width: 160, key: 'created_at',
      render: (v: string) => new Date(v).toLocaleString('zh-CN') },
    { title: '操作', width: 220, key: 'actions',
      render: (_: any, r: any) => (
        <Space>
          <Button size="small" type="link" disabled={r.status !== 'completed'}
            onClick={() => navigate(`/research/view/${r.id}`)}>
            查看报告
          </Button>
          <Button size="small" type="link" disabled={r.status !== 'completed'}
            href={`/api/v1/research/${r.id}/file?download=true`} target="_blank">
            下载 .md
          </Button>
          <Popconfirm title="删除该调研及报告文件？"
            disabled={activeStatuses.includes(r.status)}
            onConfirm={() => del.mutate(r.id)}>
            <Button size="small" type="link" danger
              disabled={activeStatuses.includes(r.status)}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ) },
  ];

  return (
    <Table rowKey="id" size="middle" loading={isLoading} columns={columns}
      dataSource={data?.items || []} pagination={{ pageSize: 10 }} />
  );
}

// ---------- 页面 ----------

export default function CompetitorResearch() {
  const [addOpen, setAddOpen] = useState(false);
  const [discoverOpen, setDiscoverOpen] = useState(false);

  return (
    <div>
      <Tabs
        defaultActiveKey="reports"
        items={[
          {
            key: 'reports',
            label: '调研报告',
            children: (
              <>
                <ResearchLauncher />
                <ScheduleCard />
                <ReportsTab />
              </>
            ),
          },
          {
            key: 'competitors',
            label: '调研对象',
            children: (
              <Tabs
                defaultActiveKey="keywords"
                items={[
                  {
                    key: 'keywords',
                    label: '关键词（赛道分析）',
                    children: <KeywordsTab />,
                  },
                  {
                    key: 'accounts',
                    label: '竞品账号（账号分析）',
                    children: (
                      <>
                        <Space style={{ marginBottom: 12 }}>
                          <Button type="primary" onClick={() => setAddOpen(true)}>添加竞品账号</Button>
                          <Button onClick={() => setDiscoverOpen(true)}>关键词发现账号</Button>
                        </Space>
                        <CompetitorsTab />
                      </>
                    ),
                  },
                ]}
              />
            ),
          },
        ]}
      />
      <AddCompetitorModal open={addOpen} onClose={() => setAddOpen(false)} />
      <DiscoverModal open={discoverOpen} onClose={() => setDiscoverOpen(false)} />
    </div>
  );
}
