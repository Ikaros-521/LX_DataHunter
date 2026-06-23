import { useState } from 'react';
import { Table, Button, Modal, Form, Select, Input, Space, Popconfirm, message, Tag } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import client from '../api/client';
import PlatformTag from '../components/PlatformTag';
import dayjs from 'dayjs';

interface Credential {
  id: number; platform: string; name: string; cookie_str: string;
  is_active: boolean; created_at: string; updated_at: string;
}

export default function CredentialManager() {
  const qc = useQueryClient();
  const [addOpen, setAddOpen] = useState(false);
  const [form] = Form.useForm();

  const { data, isLoading } = useQuery<Credential[]>({
    queryKey: ['credentials'],
    queryFn: () => client.get('/credentials').then(r => r.data),
  });

  const createMut = useMutation({
    mutationFn: (body: any) => client.post('/credentials', body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['credentials'] }); setAddOpen(false); form.resetFields(); message.success('凭证已添加'); },
  });

  const activateMut = useMutation({
    mutationFn: (id: number) => client.put(`/credentials/${id}/activate`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['credentials'] }); message.success('已切换活跃凭证'); },
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => client.delete(`/credentials/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['credentials'] }); message.success('已删除'); },
  });

  const columns = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 60 },
    { title: '平台', dataIndex: 'platform', key: 'platform', width: 100, render: (p: string) => <PlatformTag platform={p} /> },
    { title: '备注', dataIndex: 'name', key: 'name' },
    {
      title: 'Cookie', dataIndex: 'cookie_str', key: 'cookie', ellipsis: true,
      render: (s: string) => s ? `${s.slice(0, 60)}...` : '-',
    },
    {
      title: '状态', dataIndex: 'is_active', key: 'active', width: 80,
      render: (v: boolean) => v ? <Tag color="green">使用中</Tag> : <Tag>未激活</Tag>,
    },
    {
      title: '创建时间', dataIndex: 'created_at', key: 'created', width: 140,
      render: (t: string) => dayjs(t).format('MM-DD HH:mm'),
    },
    {
      title: '操作', key: 'actions', width: 200,
      render: (_: any, r: Credential) => (
        <Space>
          {!r.is_active && (
            <Button size="small" onClick={() => activateMut.mutate(r.id)}>启用</Button>
          )}
          <Popconfirm title="确认删除?" onConfirm={() => deleteMut.mutate(r.id)}>
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const handleAdd = async () => {
    await form.validateFields();
    createMut.mutate(form.getFieldsValue());
  };

  return (
    <div>
      <Button type="primary" icon={<PlusOutlined />} onClick={() => setAddOpen(true)} style={{ marginBottom: 16 }}>
        添加凭证
      </Button>

      <Table dataSource={data || []} columns={columns} rowKey="id" loading={isLoading} size="small" />

      <Modal title="添加登录凭证" open={addOpen} onOk={handleAdd} onCancel={() => setAddOpen(false)}
        confirmLoading={createMut.isPending} destroyOnClose>
        <Form form={form} layout="vertical">
          <Form.Item name="platform" label="平台" rules={[{ required: true }]}>
            <Select options={[
              { label: '抖音', value: 'douyin' },
              { label: 'B站', value: 'bilibili' },
              { label: '快手', value: 'kuaishou' },
              { label: '小红书', value: 'xiaohongshu' },
            ]} />
          </Form.Item>
          <Form.Item name="name" label="备注名">
            <Input placeholder="如: 主账号Cookie" />
          </Form.Item>
          <Form.Item name="cookie_str" label="Cookie 字符串" rules={[{ required: true }]}
            extra="从浏览器开发者工具 Network 面板复制完整的 Cookie 请求头值">
            <Input.TextArea rows={4} placeholder="name1=value1; name2=value2; ..." />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
