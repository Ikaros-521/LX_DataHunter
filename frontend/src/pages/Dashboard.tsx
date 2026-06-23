import { Row, Col, Table, Tag, List } from 'antd';
import { UserOutlined, VideoCameraOutlined, ThunderboltOutlined, ClockCircleOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import StatCard from '../components/StatCard';
import PlatformTag from '../components/PlatformTag';
import { useCreators, useSessions, useSchedules } from '../api/queries';
import dayjs from 'dayjs';

export default function Dashboard() {
  const navigate = useNavigate();
  const { data: creatorsData, isLoading: creatorsLoading } = useCreators();
  const { data: sessionsData, isLoading: sessionsLoading } = useSessions({ page_size: 10 });
  const { data: schedules } = useSchedules();

  const totalCreators = creatorsData?.total || 0;
  const totalSessions = sessionsData?.total || 0;
  const todaySessions = sessionsData?.items.filter(
    s => dayjs(s.started_at).isSame(dayjs(), 'day')
  ).length || 0;
  const activeSchedules = schedules?.filter(s => s.enabled).length || 0;

  const sessionColumns = [
    { title: '创作者', dataIndex: 'creator_nickname', key: 'creator' },
    {
      title: '平台', dataIndex: 'platform', key: 'platform',
      render: (p: string) => <PlatformTag platform={p} />,
    },
    {
      title: '状态', dataIndex: 'status', key: 'status',
      render: (s: string) => (
        <Tag color={s === 'completed' ? 'green' : s === 'running' ? 'blue' : 'red'}>{s}</Tag>
      ),
    },
    { title: '内容数', dataIndex: 'total_contents', key: 'count' },
    {
      title: '时间', dataIndex: 'started_at', key: 'time',
      render: (t: string) => dayjs(t).format('MM-DD HH:mm'),
    },
  ];

  return (
    <div>
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} md={6}>
          <StatCard title="监控创作者" value={totalCreators} icon={<UserOutlined />} color="#1677ff" loading={creatorsLoading} />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <StatCard title="总爬取次数" value={totalSessions} icon={<ThunderboltOutlined />} color="#52c41a" loading={sessionsLoading} />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <StatCard title="今日爬取" value={todaySessions} suffix="次" icon={<VideoCameraOutlined />} color="#fa8c16" />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <StatCard title="活跃定时任务" value={activeSchedules} icon={<ClockCircleOutlined />} color="#722ed1" />
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 24 }}>
        <Col xs={24} lg={16}>
          <Table
            title={() => '最近爬取活动'}
            dataSource={sessionsData?.items || []}
            columns={sessionColumns}
            rowKey="id"
            loading={sessionsLoading}
            size="small"
            onRow={(record) => ({
              onClick: () => navigate(`/creators/${record.creator_id}`),
              style: { cursor: 'pointer' },
            })}
            pagination={false}
          />
        </Col>
        <Col xs={24} lg={8}>
          <List
            header="即将执行的定时任务"
            dataSource={schedules?.filter(s => s.enabled) || []}
            renderItem={(item) => (
              <List.Item>
                <List.Item.Meta
                  title={`Creator #${item.creator_id}`}
                  description={item.next_run_at ? `下次: ${dayjs(item.next_run_at).format('MM-DD HH:mm')}` : '未调度'}
                />
                <Tag>{item.interval_minutes}分钟/次</Tag>
              </List.Item>
            )}
          />
        </Col>
      </Row>
    </div>
  );
}
