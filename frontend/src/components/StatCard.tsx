import { Card, Statistic } from 'antd';

interface StatCardProps {
  title: string;
  value: number | string;
  suffix?: string;
  icon?: React.ReactNode;
  color?: string;
  loading?: boolean;
}

export default function StatCard({ title, value, suffix, icon, color, loading }: StatCardProps) {
  return (
    <Card loading={loading}>
      <Statistic
        title={title}
        value={value}
        suffix={suffix}
        prefix={icon}
        valueStyle={color ? { color } : undefined}
      />
    </Card>
  );
}
