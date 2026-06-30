import { useState } from 'react';
import { Input, Select, DatePicker, Space, Button, Popconfirm, message } from 'antd';
import { SearchOutlined, ExportOutlined, DownloadOutlined } from '@ant-design/icons';
import ContentTable from '../components/ContentTable';
import { useContents, useCreators, usePlatforms, useBatchDownload } from '../api/queries';
import dayjs from 'dayjs';

const { RangePicker } = DatePicker;

export default function ContentSearch() {
  const [keyword, setKeyword] = useState('');
  const [searchKeyword, setSearchKeyword] = useState('');
  const [platform, setPlatform] = useState<string | undefined>();
  const [creatorId, setCreatorId] = useState<number | undefined>();
  const [contentType, setContentType] = useState<string | undefined>();
  const [sort, setSort] = useState('likes');
  const [dateRange, setDateRange] = useState<[string, string] | null>(null);
  const [page, setPage] = useState(1);
  const [selectedRowKeys, setSelectedRowKeys] = useState<number[]>([]);

  const params: Record<string, any> = {
    page, page_size: 20, sort, order: 'desc',
  };
  if (searchKeyword) params.keyword = searchKeyword;
  if (platform) params.platform = platform;
  if (creatorId) params.creator_id = creatorId;
  if (contentType) params.content_type = contentType;
  if (dateRange) { params.date_from = dateRange[0]; params.date_to = dateRange[1]; }

  const { data, isLoading } = useContents(params);
  const { data: creatorsData } = useCreators({ page_size: 200 });
  const { data: platforms } = usePlatforms();
  const batchDownloadMut = useBatchDownload();

  const handleSearch = () => {
    setSearchKeyword(keyword);
    setPage(1);
  };

  const handleExport = () => {
    if (!data?.items?.length) {
      message.warning('没有数据可导出');
      return;
    }
    const json = JSON.stringify(data.items, null, 2);
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `export_${dayjs().format('YYYYMMDD_HHmmss')}.json`;
    a.click();
    URL.revokeObjectURL(url);
    message.success('导出成功');
  };

  const handleBatchDownload = () => {
    // 仅下载 video 类型
    const videoIds = (data?.items || [])
      .filter((item) => item.content_type === 'video')
      .filter((item) => selectedRowKeys.includes(item.id))
      .map((item) => item.id);

    if (!videoIds.length) {
      message.warning('请选择至少一个视频内容进行下载');
      return;
    }

    batchDownloadMut.mutate(videoIds, {
      onSuccess: (res: any) => {
        message.success(res.data?.message || '下载任务已创建');
        setSelectedRowKeys([]);
      },
    });
  };

  return (
    <div>
      <Space wrap style={{ marginBottom: 16, width: '100%' }}>
        <Input.Search
          placeholder="搜索关键词"
          value={keyword}
          onChange={e => setKeyword(e.target.value)}
          onSearch={handleSearch}
          style={{ width: 250 }}
          enterButton={<SearchOutlined />}
        />
        <Select
          allowClear placeholder="平台" style={{ width: 100 }}
          value={platform} onChange={setPlatform}
          options={(platforms || []).map(p => ({ label: p.name, value: p.id }))}
        />
        <Select
          allowClear showSearch placeholder="创作者" style={{ width: 200 }}
          value={creatorId} onChange={setCreatorId}
          filterOption={(input, option) => (option?.label as string || '').includes(input)}
          options={(creatorsData?.items || []).map(c => ({
            label: `${c.nickname || c.platform_uid}`,
            value: c.id,
          }))}
        />
        <Select
          allowClear placeholder="类型" style={{ width: 100 }}
          value={contentType} onChange={setContentType}
          options={[
            { label: '视频', value: 'video' },
            { label: '图文', value: 'image' },
          ]}
        />
        <Select
          value={sort} onChange={setSort} style={{ width: 100 }}
          options={[
            { label: '按点赞', value: 'likes' },
            { label: '按评论', value: 'comments' },
            { label: '按转发', value: 'shares' },
            { label: '按播放', value: 'plays' },
            { label: '按时间', value: 'create_time' },
          ]}
        />
        <RangePicker
          onChange={(_, dateStrings) => {
            if (dateStrings[0] && dateStrings[1]) {
              setDateRange([dateStrings[0], dateStrings[1]]);
            } else {
              setDateRange(null);
            }
          }}
        />
        <Button icon={<ExportOutlined />} onClick={handleExport}>导出</Button>
      </Space>

      {selectedRowKeys.length > 0 && (
        <Space style={{ marginBottom: 16 }}>
          <Popconfirm
            title={`确认下载选中的 ${selectedRowKeys.length} 个视频?`}
            description="仅视频类型内容会被加入下载队列"
            onConfirm={handleBatchDownload}
          >
            <Button type="primary" icon={<DownloadOutlined />}>
              批量下载 {selectedRowKeys.length} 条
            </Button>
          </Popconfirm>
        </Space>
      )}

      <ContentTable
        data={data?.items || []}
        loading={isLoading}
        total={data?.total || 0}
        page={page}
        onPageChange={(p) => { setPage(p); setSelectedRowKeys([]); }}
        rowSelection={{
          selectedRowKeys,
          onChange: (keys) => setSelectedRowKeys(keys as number[]),
        }}
      />
    </div>
  );
}
