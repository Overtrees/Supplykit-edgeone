import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ChartRow3 } from '../pages/dashboard/ChartRows'

/**
 * 多码断货提示(2026-11-06 S3 码级复核的呈现层)
 *
 * 为什么单独测这一条: 后端 `stockOverview.code_out_count` 与前端读取的键名是**手写的**
 * 两个字符串, 写错一个就静默恒 0 —— 页面上什么都不显示, 也没人会发现。
 * 线上真实渠道当前 code_out=0(多码 SKU 都整盘断货, 判 out 不计), 所以截图看不到,
 * 只能靠这条用例证明"有值时确实会显示"。
 */
describe('ChartRow3 多码断货提示', () => {
  it('codeOutCount > 0 时显示提示与数量', () => {
    render(<ChartRow3 lowStockShown={[]} procList={[]} lowStockTotal={0} codeOutCount={3} />)
    expect(screen.getByText(/3/)).toBeInTheDocument()
    expect(screen.getByText(/多码|multi-code/i)).toBeInTheDocument()
  })

  it('codeOutCount = 0 时不显示(不制造噪音)', () => {
    const { container } = render(
      <ChartRow3 lowStockShown={[]} procList={[]} lowStockTotal={0} codeOutCount={0} />,
    )
    expect(container.textContent).not.toMatch(/多码 SKU 部分码断货/)
  })
})
