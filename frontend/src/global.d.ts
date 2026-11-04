/** 清洗页当前列映射的一个单元格(供模板内嵌自定义字段) */
interface MappingCell {
  target?: string
  [k: string]: unknown
}

interface Window {
  __page?: string
  __setPage?: (p: string) => void
  __curMp?: Record<string, MappingCell>
}
