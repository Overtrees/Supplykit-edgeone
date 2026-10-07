import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToastProvider, useToast } from '../components/Toast'

function TestButton() {
  const toast = useToast()
  return <button onClick={() => toast.success('测试消息')}>显示Toast</button>
}

describe('Toast 组件', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  it('显示成功消息', async () => {
    render(
      <ToastProvider>
        <TestButton />
      </ToastProvider>,
    )
    await userEvent.click(screen.getByText('显示Toast'))
    expect(screen.getByText('测试消息')).toBeInTheDocument()
  })

  it('3秒后自动消失', async () => {
    render(
      <ToastProvider>
        <TestButton />
      </ToastProvider>,
    )
    await userEvent.click(screen.getByText('显示Toast'))
    expect(screen.getByText('测试消息')).toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    expect(screen.queryByText('测试消息')).not.toBeInTheDocument()
  })

  it('支持撤销按钮', async () => {
    const handler = vi.fn()
    function TestUndo() {
      const toast = useToast()
      return (
        <button
          onClick={() =>
            toast.add({
              type: 'success',
              title: '已删除',
              duration: 5000,
              action: { label: '撤销', handler },
            })
          }
        >
          删除
        </button>
      )
    }
    render(
      <ToastProvider>
        <TestUndo />
      </ToastProvider>,
    )
    await userEvent.click(screen.getByText('删除'))
    expect(screen.getByText('已删除')).toBeInTheDocument()
    expect(screen.getByText('撤销')).toBeInTheDocument()
    await userEvent.click(screen.getByText('撤销'))
    expect(handler).toHaveBeenCalled()
  })
})
