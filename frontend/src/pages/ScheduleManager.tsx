import { useState, useMemo } from 'react';
import { Table, Button, Modal, Select, Switch, Space, Popconfirm, message, Input } from 'antd';
import { ClockCircleOutlined, EditOutlined, DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import client from '../api/client';
import PlatformTag from '../components/PlatformTag';
import dayjs from 'dayjs';
import type { Schedule } from '../api/queries';

function isSearchCreator(c: any): boolean {
  return c?.platform_uid?.startsWith('__hot__') && c?.platform_uid?.includes('_search');
}

export default function ScheduleManager() {
  const qc = useQueryClient();
  const [editOpen, setEditOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [editingSchedule, setEditingSchedule] = useState<Schedule | null>(null);
  const [editInterval, setEditInterval] = useState(360);
  const [editEnabled, setEditEnabled] = useState(true);
  const [editKeyword, setEditKeyword] = useState('');
  const [addCreatorId, setAddCreatorId] = useState<number | null>(null);
  const [addInterval, setAddInterval] = useState(360);
  const [addKeyword, setAddKeyword] = useState('');

  const { data: schedules, isLoading } = useQuery<Schedule[]>({
    queryKey: ['schedules'],
    queryFn: () => client.get('/schedules').then(r => r.data),
  });

  const { data: creatorsData } = useQuery<any>({
    queryKey: ['creators'],
    queryFn: () => client.get('/creators?page_size=200').then(r => r.data),
  });

  const upsertMut = useMutation({
    mutationFn: ({ creatorId, data }: { creatorId: number; data: any }) =>
      client.put(`/schedules/${creatorId}`, data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['schedules'] }); setEditOpen(false); message.success('已保存'); },
  });

  const deleteMut = useMutation({
    mutationFn: (creatorId: number) => client.delete(`/schedules/${creatorId}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['schedules'] }); message.success('已删除'); },
  });

  const getCreator = (id: number) => creatorsData?.items?.find((c: any) => c.id === id);

  const handleEdit = (schedule: Schedule) => {
    setEditingSchedule(schedule);
    setEditInterval(schedule.interval_minutes);
    setEditEnabled(schedule.enabled);
    setEditKeyword(schedule.extra_config?.keyword || '');
    setEditOpen(true);
  };

  // 还没配置定时的创作者
  const scheduledIds = new Set((schedules || []).map(s => s.creator_id));
  const unscheduledCreators = useMemo(() =>
    (creatorsData?.items || []).filter((c: any) => !scheduledIds.has(c.id)),
    [creatorsData, scheduledIds]
  );

  const handleSave = () => {
    if (!editingSchedule) return;
    const data: any = { interval_minutes: editInterval, enabled: editEnabled };
    const c = getCreator(editingSchedule.creator_id);
    if (isSearchCreator(c)) data.keyword = editKeyword;
    upsertMut.mutate({ creatorId: editingSchedule.creator_id, data });
  };

  const handleAdd = () => {
    if (!addCreatorId) { message.warning('请选择创作者'); return; }
    const data: any = { interval_minutes: addInterval, enabled: true };
    const c = getCreator(addCreatorId);
    if (isSearchCreator(c)) data.keyword = addKeyword;
    upsertMut.mutate({ creatorId: addCreatorId, data });
  };

  const columns = [
    { title: '创作者', dataIndex: 'creator_id', key: 'creator',
      render: (id: number) => {
        const c = getCreator(id);
        return c ? <span>{c.nickname || c.platform_uid} <PlatformTag platform={c.platform} /></span> : `#${id}`;
      }
    },
    {
      title: '间隔', dataIndex: 'interval_minutes', key: 'interval',
      render: (m: number) => {
        if (m < 60) return `${m} 分钟`;
        if (m < 1440) return `${Math.round(m / 60)} 小时`;
        return `${Math.round(m / 1440)} 天`;
      },
    },
    {
      title: '状态', dataIndex: 'enabled', key: 'enabled',
      render: (v: boolean) => v
        ? <span style={{ color: '#52c41a' }}>● 运行中</span>
        : <span style={{ color: '#999' }}>○ 已暂停</span>,
    },
    {
      title: '上次执行', dataIndex: 'last_run_at', key: 'last',
      render: (t: string) => t ? dayjs(t).format('MM-DD HH:mm') : '未执行',
    },
    {
      title: '下次执行', dataIndex: 'next_run_at', key: 'next',
      render: (t: string) => t ? dayjs(t).format('MM-DD HH:mm') : '待调度',
    },
    {
      title: '操作', key: 'actions', width: 150,
      render: (_: any, r: Schedule) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => handleEdit(r)}>编辑</Button>
          <Popconfirm title="确认删除?" onConfirm={() => deleteMut.mutate(r.creator_id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const intervalOptions = [
    { label: '30 分钟', value: 30 },
    { label: '1 小时', value: 60 },
    { label: '3 小时', value: 180 },
    { label: '6 小时', value: 360 },
    { label: '12 小时', value: 720 },
    { label: '1 天', value: 1440 },
    { label: '3 天', value: 4320 },
    { label: '1 周', value: 10080 },
  ];

  return (
    <div>
      <h3><ClockCircleOutlined /> 定时任务管理</h3>
      <div style={{ color: '#888', marginBottom: 16, fontSize: 13 }}>
        系统每分钟检查一次到期任务，自动执行爬取。配置间隔从上次执行完成开始计算。
      </div>

      <Button type="primary" icon={<PlusOutlined />}
        onClick={() => { setAddOpen(true); setAddCreatorId(null); setAddInterval(360); }}
        disabled={unscheduledCreators.length === 0}
        style={{ marginBottom: 16 }}>
        添加定时任务
      </Button>
      {unscheduledCreators.length === 0 && (
        <span style={{ color: '#888', marginLeft: 8, fontSize: 13 }}>所有创作者已配置定时</span>
      )}

      <Table dataSource={schedules || []} columns={columns} rowKey="id" loading={isLoading} size="small" />

      <Modal title="添加定时任务" open={addOpen} onOk={handleAdd}
        onCancel={() => setAddOpen(false)} confirmLoading={upsertMut.isPending}>
        <div style={{ marginBottom: 16 }}>
          <div style={{ marginBottom: 8 }}>选择创作者</div>
          <Select showSearch placeholder="搜索创作者" value={addCreatorId} onChange={setAddCreatorId}
            style={{ width: '100%' }}
            filterOption={(input, option) => (option?.label as string || '').includes(input)}
            options={unscheduledCreators.map((c: any) => ({
              label: `${c.nickname || c.platform_uid} (${c.platform})`,
              value: c.id,
            }))} />
        </div>
        <div style={{ marginBottom: 16 }}>
          <div style={{ marginBottom: 8 }}>执行间隔</div>
          <Select value={addInterval} onChange={setAddInterval}
            options={intervalOptions} style={{ width: '100%' }} />
        </div>
        {addCreatorId && isSearchCreator(getCreator(addCreatorId)) && (
          <div style={{ marginBottom: 16 }}>
            <div style={{ marginBottom: 8 }}>搜索关键词</div>
            <Input value={addKeyword} onChange={e => setAddKeyword(e.target.value)}
              placeholder="输入搜索关键词（必填）" />
          </div>
        )}
      </Modal>

      <Modal title="编辑定时任务" open={editOpen} onOk={handleSave}
        onCancel={() => setEditOpen(false)} confirmLoading={upsertMut.isPending}>
        <div style={{ marginBottom: 16 }}>
          <div style={{ marginBottom: 8 }}>执行间隔</div>
          <Select value={editInterval} onChange={setEditInterval}
            options={intervalOptions} style={{ width: '100%' }} />
        </div>
        {editingSchedule && isSearchCreator(getCreator(editingSchedule.creator_id)) && (
          <div style={{ marginBottom: 16 }}>
            <div style={{ marginBottom: 8 }}>搜索关键词</div>
            <Input value={editKeyword} onChange={e => setEditKeyword(e.target.value)}
              placeholder="输入搜索关键词（必填）" />
          </div>
        )}
        <div>
          <div style={{ marginBottom: 8 }}>启用状态</div>
          <Switch checked={editEnabled} onChange={setEditEnabled}
            checkedChildren="启用" unCheckedChildren="暂停" />
        </div>
      </Modal>
    </div>
  );
}
