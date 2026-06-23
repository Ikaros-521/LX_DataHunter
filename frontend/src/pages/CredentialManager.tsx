import { useState } from 'react';
import { Table, Button, Modal, Form, Select, Input, Space, Popconfirm, message, Tag, Tabs, Spin, Alert } from 'antd';
import { PlusOutlined, ChromeOutlined, EditOutlined } from '@ant-design/icons';
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
  const [autoLoading, setAutoLoading] = useState(false);
  const [autoResult, setAutoResult] = useState<string | null>(null);
  const [form] = Form.useForm();

  const { data, isLoading } = useQuery<Credential[]>({
    queryKey: ['credentials'],
    queryFn: () => client.get('/credentials').then(r => r.data),
  });

  const createMut = useMutation({
    mutationFn: (body: any) => client.post('/credentials', body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['credentials'] }); setAddOpen(false); form.resetFields(); setAutoResult(null); message.success('凭证已添加'); },
  });

  const activateMut = useMutation({
    mutationFn: (id: number) => client.put(`/credentials/${id}/activate`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['credentials'] }); message.success('已切换活跃凭证'); },
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => client.delete(`/credentials/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['credentials'] }); message.success('已删除'); },
  });

  const handleAutoLogin = async () => {
    const platform = form.getFieldValue('platform');
    if (!platform) { message.warning('请先选择平台'); return; }
    setAutoLoading(true);
    setAutoResult(null);
    try {
      const res = await client.post(`/credentials/auto-login?platform=${platform}&headless=false`);
      const { cookie_str, cookie_count } = res.data;
      setAutoResult(cookie_str);
      message.success(`捕获到 ${cookie_count} 个 Cookie`);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '浏览器登录失败');
    } finally {
      setAutoLoading(false);
    }
  };

  const handleSaveAuto = () => {
    if (!autoResult) return;
    const platform = form.getFieldValue('platform');
    const name = form.getFieldValue('name') || `${platform}_auto`;
    createMut.mutate({ platform, name, cookie_str: autoResult });
  };

  const handleManualSave = async () => {
    await form.validateFields();
    createMut.mutate({
      platform: form.getFieldValue('platform'),
      name: form.getFieldValue('name'),
      cookie_str: form.getFieldValue('cookie_str'),
    });
  };

  const columns = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 60 },
    { title: '平台', dataIndex: 'platform', key: 'platform', width: 100, render: (p: string) => <PlatformTag platform={p} /> },
    { title: '备注', dataIndex: 'name', key: 'name' },
    {
      title: 'Cookie', dataIndex: 'cookie_str', key: 'cookie', ellipsis: true, width: 200,
      render: (s: string) => s ? `${s.slice(0, 50)}...` : '-',
    },
    {
      title: '状态', dataIndex: 'is_active', key: 'active', width: 80,
      render: (v: boolean) => v ? <Tag color="green">使用中</Tag> : <Tag>未激活</Tag>,
    },
    {
      title: '创建', dataIndex: 'created_at', key: 'created', width: 130,
      render: (t: string) => dayjs(t).format('MM-DD HH:mm'),
    },
    {
      title: '操作', key: 'actions', width: 150,
      render: (_: any, r: Credential) => (
        <Space>
          {!r.is_active && <Button size="small" onClick={() => activateMut.mutate(r.id)}>启用</Button>}
          <Popconfirm title="确认删除?" onConfirm={() => deleteMut.mutate(r.id)}>
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // 平台选项
  const platformOptions = [
    { label: '抖音', value: 'douyin' },
    { label: 'B站', value: 'bilibili' },
    { label: '快手', value: 'kuaishou' },
    { label: '小红书', value: 'xiaohongshu' },
  ];

  const tabItems = [
    {
      key: 'browser',
      label: <span><ChromeOutlined /> 浏览器登录</span>,
      children: (
        <div>
          <Button type="primary" icon={<ChromeOutlined />} loading={autoLoading}
            onClick={handleAutoLogin} block size="large">
            打开浏览器登录
          </Button>
          <div style={{ color: '#888', fontSize: 12, marginTop: 8 }}>
            浏览器窗口打开后，请手动完成登录（扫码或密码）。登录成功后 Cookie 会自动捕获。
          </div>

          {autoLoading && (
            <div style={{ textAlign: 'center', marginTop: 16 }}>
              <Spin tip="等待登录中... (最多5分钟)" />
            </div>
          )}

          {autoResult && (
            <Alert type="success" showIcon style={{ marginTop: 16 }}
              message="Cookie 已捕获" description={
                <div>
                  <div style={{ wordBreak: 'break-all', maxHeight: 100, overflow: 'auto', marginBottom: 8, fontSize: 12, color: '#666' }}>
                    {autoResult.slice(0, 300)}...
                  </div>
                  <Button type="primary" size="small" onClick={handleSaveAuto}>保存此凭证</Button>
                </div>
              } />
          )}
        </div>
      ),
    },
    {
      key: 'manual',
      label: <span><EditOutlined /> 手动输入</span>,
      children: (
        <div>
          <Form.Item name="cookie_str" label="Cookie 字符串" rules={[{ required: true }]}
            extra="从浏览器 F12 → Network → 复制请求头中的完整 Cookie 值">
            <Input.TextArea rows={4} placeholder="name1=value1; name2=value2; ..." />
          </Form.Item>
          <Button type="primary" onClick={handleManualSave} block>保存</Button>
        </div>
      ),
    },
  ];

  return (
    <div>
      <Button type="primary" icon={<PlusOutlined />} onClick={() => { setAddOpen(true); setAutoResult(null); form.resetFields(); }}
        style={{ marginBottom: 16 }}>
        添加凭证
      </Button>

      <Table dataSource={data || []} columns={columns} rowKey="id" loading={isLoading} size="small" />

      <Modal title="添加登录凭证" open={addOpen} onCancel={() => { setAddOpen(false); setAutoResult(null); }}
        footer={null} width={560} destroyOnHidden>
        <Form form={form} layout="vertical">
          <Form.Item name="platform" label="平台" rules={[{ required: true, message: '请选择平台' }]}>
            <Select placeholder="选择平台" options={platformOptions} />
          </Form.Item>
          <Form.Item name="name" label="备注名（可选）">
            <Input placeholder="如: 主账号" />
          </Form.Item>
          <Tabs items={tabItems} />
        </Form>
      </Modal>
    </div>
  );
}
