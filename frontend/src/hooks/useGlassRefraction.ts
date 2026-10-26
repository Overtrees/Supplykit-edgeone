import { useEffect } from 'react'

/**
 * SVG 真折射增强(2026-10-26 映射概念稿): 玻璃按钮/浮层面板的色差折射——feDisplacementMap 位移图
 * 生成 per 元素 lens 折射(边缘弯曲 + R/B 色差 1.5px screen 混合), Chrome/Edge 渐进增强;
 * 看板卡片不用(数据可读性优先); 不支持 backdrop-filter: url() 的环境静默跳过
 */
const SVG_NS = 'http://www.w3.org/2000/svg'

function createDistortionFilter(id: string): {
  filter: SVGFilterElement
  feImage: SVGFEImageElement
} {
  const filter = document.createElementNS(SVG_NS, 'filter')
  filter.setAttribute('id', id)
  filter.setAttribute('x', '-20%')
  filter.setAttribute('y', '-20%')
  filter.setAttribute('width', '140%')
  filter.setAttribute('height', '140%')
  filter.setAttribute('color-interpolation-filters', 'sRGB')

  const feImage = document.createElementNS(SVG_NS, 'feImage')
  feImage.setAttribute('result', 'map')
  feImage.setAttribute('preserveAspectRatio', 'none')

  const feDisplace = document.createElementNS(SVG_NS, 'feDisplacementMap')
  feDisplace.setAttribute('in', 'SourceGraphic')
  feDisplace.setAttribute('in2', 'map')
  feDisplace.setAttribute('scale', '32')
  feDisplace.setAttribute('xChannelSelector', 'R')
  feDisplace.setAttribute('yChannelSelector', 'G')
  feDisplace.setAttribute('result', 'displaced')

  const mk = (tag: string, attrs: Record<string, string>) => {
    const el = document.createElementNS(SVG_NS, tag)
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v)
    return el
  }
  const feRed = mk('feColorMatrix', {
    in: 'displaced',
    type: 'matrix',
    values: '1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0',
    result: 'red',
  })
  const feRedShift = mk('feOffset', { in: 'red', dx: '1.5', dy: '0', result: 'redShift' })
  const feBlue = mk('feColorMatrix', {
    in: 'displaced',
    type: 'matrix',
    values: '0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0',
    result: 'blue',
  })
  const feBlueShift = mk('feOffset', { in: 'blue', dx: '-1.5', dy: '0', result: 'blueShift' })
  const feBlend1 = mk('feBlend', { in: 'redShift', in2: 'displaced', mode: 'screen', result: 'rg' })
  const feBlend2 = mk('feBlend', { in: 'blueShift', in2: 'rg', mode: 'screen' })

  for (const n of [feImage, feDisplace, feRed, feRedShift, feBlue, feBlueShift, feBlend1, feBlend2])
    filter.appendChild(n)
  // 确保 svg defs 容器存在(页面无则创建, 隐藏零尺寸)
  let defs = document.querySelector('svg defs')
  if (!defs) {
    const svg = document.createElementNS(SVG_NS, 'svg')
    svg.setAttribute('aria-hidden', 'true')
    svg.style.position = 'absolute'
    svg.style.width = '0'
    svg.style.height = '0'
    defs = document.createElementNS(SVG_NS, 'defs')
    svg.appendChild(defs)
    document.body.appendChild(svg)
  }
  defs.appendChild(filter)
  return { filter, feImage }
}

function generateLensMap(el: HTMLElement, feImage: SVGFEImageElement) {
  const rect = el.getBoundingClientRect()
  if (rect.width === 0 || rect.height === 0) return
  const w = Math.max(1, Math.floor(rect.width / 2))
  const h = Math.max(1, Math.floor(rect.height / 2))
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  const imgData = ctx.createImageData(w, h)
  const data = imgData.data
  const cx = w / 2
  const cy = h / 2
  const radius = h / 2
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const dx = x - cx
      const dy = y - cy
      let dist = 0
      let nx = 0
      let ny = 0
      if (Math.abs(dx) > cx - radius) {
        const cornerX = dx > 0 ? cx - radius : -(cx - radius)
        const cdx = dx - cornerX
        const dLen = Math.sqrt(cdx * cdx + dy * dy)
        dist = dLen - radius
        nx = cdx / (dLen || 1)
        ny = dy / (dLen || 1)
      } else {
        dist = Math.abs(dy) - cy
        ny = dy > 0 ? 1 : -1
      }
      const edgeT = Math.max(0, Math.min(1, 1 + dist / 24))
      const bend = Math.pow(1 - edgeT, 2)
      const i = (y * w + x) * 4
      data[i] = Math.floor(128 + nx * bend * 127)
      data[i + 1] = Math.floor(128 + ny * bend * 127)
      data[i + 2] = 0
      data[i + 3] = 255
    }
  }
  ctx.putImageData(imgData, 0, 0)
  feImage.setAttribute('href', canvas.toDataURL())
}

export default function useGlassRefraction(): void {
  useEffect(() => {
    // 渐进增强: 仅支持 backdrop-filter: url(#...) 的环境(Chrome/Edge)
    const testEl = document.createElement('div')
    testEl.style.setProperty('-webkit-backdrop-filter', 'url(#gg-test)')
    testEl.style.setProperty('backdrop-filter', 'url(#gg-test)')
    const supported = testEl.style.getPropertyValue('backdrop-filter') !== ''
    if (!supported) return
    const targets = Array.from(
      document.querySelectorAll<HTMLElement>(
        '.glass-edge, .menu-btn, .back-btn, .hammer-icon-btn, .page-btn, .header-status',
      ),
    ).filter(el => el.getBoundingClientRect().width > 0)
    if (!targets.length) return
    const jobs = targets.map((el, i) => {
      const id = 'gg-lens-' + i + '-' + Math.random().toString(36).slice(2, 7)
      const { filter, feImage } = createDistortionFilter(id)
      el.style.setProperty('--glass-url', `url(#${id})`)
      el.classList.add('is-refraction-active')
      const rebuild = () => generateLensMap(el, feImage)
      rebuild()
      return { el, filter, rebuild }
    })
    let timer = 0
    const onResize = () => {
      clearTimeout(timer)
      timer = window.setTimeout(() => jobs.forEach(j => j.rebuild()), 150)
    }
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      clearTimeout(timer)
      jobs.forEach(j => {
        j.el.classList.remove('is-refraction-active')
        j.el.style.removeProperty('--glass-url')
        try {
          j.filter.remove()
        } catch {}
      })
    }
  }, [])
}
