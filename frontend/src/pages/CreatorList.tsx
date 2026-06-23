import { useState } from 'react';
import { Table, Button, Modal, Form, Select, Input, Space, Popconfirm, message, Tag } from 'antd';
import { PlusOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import PlatformTag from '../components/PlatformTag';
import { useCreators, useAddCreator, useDeleteCreator, useTriggerCrawl, usePlatforms } from '../api/queries';
import dayjs from 'dayjs';

export default function CreatorList() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [platformFilter, setPlatformFilter] = useState<string | undefined>();
  const [addModalOpen, setAddModalOpen] = useState(false);
  const [form] = Form.useForm();

  const { data, isLoading } = useCreators({ page, page_size: 20, platform: platformFilter });
  const { data: platforms } = usePlatforms();
  const addMutation = useAddCreator();
  const deleteMutation = useDeleteCreator();
  const crawlMutation = useTriggerCrawl();

  const activePlatforms = platforms?.filter(p => p.status === 'active') || [];

  const columns = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 60 },
    {
      title: '平台', dataIndex: 'platform', key: 'platform', width: 100,
      render: (p: string) => <PlatformTag platform={p} />,
    },
    { title: '昵称', dataIndex: 'nickname', key: 'nickname', render: (n: string) => n || '(待爬取)' },
    { title: '粉丝', dataIndex: 'follower_count', key: 'follower', render: (n: number) => n ? n.toLocaleString() : '-' },
    {
      title: '最近爬取', key: 'latest',
      render: (_: any, record: any) => record.latest_stats
        ? dayjs(record.latest_stats.fetched_at).format('MM-DD HH:mm')
        : '未爬取',
    },
    {
      title: '定时', key: 'schedule',
      render: (_: any, record: any) => record.schedule?.enabled
        ? <Tag color="green">{record.schedule.interval_minutes}分钟</Tag>
        : <Tag>未设置</Tag>,
    },
    {
      title: '操作', key: 'actions', width: 250,
      render: (_: any, record: any) => (
        <Space>
          <Button size="small" onClick={() => navigate(`/creators/${record.id}`)}>详情</Button>
          <Button size="small" icon={<ThunderboltOutlined />} loading={crawlMutation.isPending}
            onClick={() => crawlMutation.mutate({ creatorId: record.id }, {
              onSuccess: (res: any) => message.success(`爬取已启动 (Session #${res.data.session_id})`),
            })}>爬取</Button>
          <Popconfirm title="确认删除?" onConfirm={() => deleteMutation.mutate(record.id)}>
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const handleAdd = async () => {
    try {
      const values = await form.validateFields();
      await addMutation.mutateAsync(values);
      message.success('创作者已添加');
      setAddModalOpen(false);
      form.resetFields();
    } catch (e) {
      // validation error
    }
  };

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Select
          allowClear
          placeholder="筛选平台"
          style={{ width: 120 }}
          onChange={(v) => setPlatformFilter(v)}
          options={(platforms || []).map(p => ({ label: p.name, value: p.id }))}
        />
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setAddModalOpen(true)}>
          添加创作者
        </Button>
      </Space>

      <Table
        dataSource={data?.items || []}
        columns={columns}
        rowKey="id"
        loading={isLoading}
        pagination={{
          current: page,
          total: data?.total || 0,
          onChange: setPage,
        }}
      />

      <Modal
        title="添加创作者"
        open={addModalOpen}
        onOk={handleAdd}
        onCancel={() => setAddModalOpen(false)}
        confirmLoading={addMutation.isPending}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="platform" label="平台" rules={[{ required: true }]}>
            <Select options={activePlatforms.map(p => ({ label: p.name, value: p.id }))} />
          </Form.Item>
          <Form.Item name="platform_uid" label="平台UID" rules={[{ required: true }]}
            extra="抖音: 从分享链接 /user/ 后获取。B站: 用户空间URL中的数字ID"
          >
            <Input placeholder="输入平台用户唯一标识" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
