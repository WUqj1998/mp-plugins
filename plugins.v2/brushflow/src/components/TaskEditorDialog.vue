<script setup>
import { computed, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'
import { cloneTask, normalizeTask } from '../utils'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  task: { type: Object, default: () => ({}) },
  sites: { type: Array, default: () => [] },
  downloaders: { type: Array, default: () => [] },
  globalDynamicDelete: { type: Boolean, default: false },
  saving: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue', 'save'])
const display = useDisplay()
const formRef = ref(null)
const activeTab = ref('base')
const localTask = ref(cloneTask())

const dialogTitle = computed(() => (localTask.value.id ? '编辑刷流任务' : '新建刷流任务'))
const siteName = computed(() => props.sites.find(item => item.value === Number(localTask.value.site_id))?.title || '未选择')
const scheduleText = computed(() => localTask.value.cron || `每 ${localTask.value.brush_interval || 10} 分钟`)

// 每次打开弹窗都从服务端任务快照重新创建本地草稿。
watch(
  () => props.modelValue,
  visible => {
    if (!visible) return
    localTask.value = cloneTask(props.task)
    activeTab.value = 'base'
  },
)

// 关闭编辑器并丢弃尚未保存的草稿。
function closeDialog() {
  emit('update:modelValue', false)
}

// 校验必填项后提交标准化任务数据。
async function saveTask() {
  const result = await formRef.value?.validate()
  if (result && !result.valid) return
  emit('save', normalizeTask(localTask.value))
}
</script>

<template>
  <VDialog
    :model-value="modelValue"
    scrollable
    :fullscreen="display.smAndDown.value"
    max-width="74rem"
    @update:model-value="value => emit('update:modelValue', value)"
  >
    <VCard class="brushflow-editor">
      <VToolbar color="transparent" density="comfortable" class="brushflow-editor__toolbar">
        <VToolbarTitle>{{ dialogTitle }}</VToolbarTitle>
        <VSpacer />
        <VBtn color="primary" variant="flat" prepend-icon="mdi-content-save" :loading="saving" @click="saveTask">
          保存任务
        </VBtn>
        <VBtn icon="mdi-close" variant="text" aria-label="关闭" @click="closeDialog" />
      </VToolbar>

      <VCardText class="brushflow-editor__body">
        <VForm ref="formRef" class="brushflow-editor__form" @submit.prevent="saveTask">
          <VTabs
            v-model="activeTab"
            :direction="display.mdAndUp.value ? 'vertical' : 'horizontal'"
            color="primary"
            class="brushflow-editor__tabs"
          >
            <VTab value="base" prepend-icon="mdi-calendar-clock">基础与调度</VTab>
            <VTab value="selection" prepend-icon="mdi-filter-cog-outline">选种规则</VTab>
            <VTab value="limits" prepend-icon="mdi-gauge">运行限额</VTab>
            <VTab value="speedlimit" prepend-icon="mdi-speedometer">任务限速</VTab>
            <VTab value="delete" prepend-icon="mdi-delete-clock-outline">删种规则</VTab>
            <VTab value="advanced" prepend-icon="mdi-tune-variant">高级</VTab>
          </VTabs>

          <VDivider :vertical="display.mdAndUp.value" />

          <VWindow v-model="activeTab" :touch="false" class="brushflow-editor__window">
            <VWindowItem value="base">
              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">任务身份</div>
                    <div class="text-body-2 text-medium-emphasis">每个任务绑定一个站点和下载器</div>
                  </div>
                  <VChip size="small" color="primary" variant="tonal">必填</VChip>
                </header>
                <VRow>
                  <VCol cols="12" md="6">
                    <VTextField
                      v-model="localTask.name"
                      label="任务名称"
                      :rules="[value => !!String(value || '').trim() || '请输入任务名称']"
                    />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VSelect
                      v-model="localTask.site_id"
                      :items="sites"
                      label="站点"
                      :rules="[value => !!value || '请选择站点']"
                    />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VSelect
                      v-model="localTask.downloader"
                      :items="downloaders"
                      label="下载器"
                      :rules="[value => !!value || '请选择下载器']"
                    />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VTextField v-model="localTask.save_path" label="保存目录" placeholder="留空使用下载器默认目录" />
                  </VCol>
                </VRow>
                <div class="editor-switches">
                  <VSwitch v-model="localTask.enabled" label="启用任务" color="primary" hide-details inset />
                  <VSwitch v-model="localTask.notify" label="发送通知" color="primary" hide-details inset />
                </div>
              </section>

              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">刷新计划</div>
                    <div class="text-body-2 text-medium-emphasis">刷流刷新和下载状态检查分别调度</div>
                  </div>
                </header>
                <VRow>
                  <VCol cols="12" md="6">
                    <VTextField
                      v-model.number="localTask.brush_interval"
                      type="number"
                      min="1"
                      max="1440"
                      label="刷流刷新周期（分钟）"
                    />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VTextField
                      v-model.number="localTask.check_interval"
                      type="number"
                      min="1"
                      max="1440"
                      label="状态检查周期（分钟）"
                    />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VTextField v-model="localTask.cron" label="CRON 表达式" placeholder="留空使用固定刷新周期" />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VTextField
                      v-model="localTask.active_time_range"
                      label="开启时间段"
                      placeholder="如 00:00-08:00"
                    />
                  </VCol>
                </VRow>
              </section>

              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">站点分享率控制</div>
                    <div class="text-body-2 text-medium-emphasis">根据最新站点统计自动等待或恢复刷流</div>
                  </div>
                </header>
                <VSwitch
                  v-model="localTask.site_ratio_control"
                  label="启用站点分享率控制"
                  color="primary"
                  hide-details
                  inset
                />
                <VRow v-if="localTask.site_ratio_control">
                  <VCol cols="12" md="6">
                    <VTextField
                      v-model.number="localTask.site_ratio_target"
                      type="number"
                      min="0.01"
                      step="0.01"
                      label="目标分享率"
                      :rules="[value => Number(value) > 0 || '请输入大于 0 的目标分享率']"
                    />
                  </VCol>
                </VRow>
              </section>

              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">站点下载量控制</div>
                    <div class="text-body-2 text-medium-emphasis">根据最新站点统计自动限制新增下载</div>
                  </div>
                </header>
                <VSwitch
                  v-model="localTask.download_limit_enabled"
                  label="启用站点下载量控制"
                  color="primary"
                  hide-details
                  inset
                />
                <VRow v-if="localTask.download_limit_enabled">
                  <VCol cols="12" md="6">
                    <VTextField
                      v-model.number="localTask.download_limit"
                      type="number"
                      min="0.01"
                      step="0.01"
                      label="站点下载量上限（GB）"
                      :rules="[value => Number(value) > 0 || '请输入大于 0 的下载量上限']"
                    />
                  </VCol>
                </VRow>
              </section>

              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">状态检查优化</div>
                  </div>
                </header>
                <VSwitch
                  v-model="localTask.skip_check_no_active"
                  label="无活跃种子时跳过状态检查"
                  color="primary"
                  hide-details
                  inset
                />
              </section>

              <!-- v5.5.2: TTGL积分商店折扣 - 仅在选择站点为听听歌时显示；v5.5.6 新增档位选择 -->
              <section v-if="siteName === '听听歌'" class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">积分商店折扣</div>
                    <div class="text-body-2 text-medium-emphasis">自动购买并使用下载折扣（有库存直接用，无库存自动购买；48小时有效）</div>
                  </div>
                </header>
                <VSwitch
                  v-model="localTask.ttgl_discount_enabled"
                  label="启用自动购买并使用下载折扣"
                  color="primary"
                  hide-details
                  inset
                />
                <VRow v-if="localTask.ttgl_discount_enabled" class="mt-3">
                  <VCol cols="12" md="6">
                    <VSelect
                      v-model="localTask.ttgl_discount_tier"
                      label="折扣档位"
                      :items="[
                        { title: '30%下载（5000积分）', value: '30' },
                        { title: '50%下载（2000积分）', value: '50' },
                        { title: '免费FL（10000积分）', value: 'fl' },
                      ]"
                    />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VTextField
                      v-model.number="localTask.ttgl_discount_min_size"
                      type="number"
                      min="0.01"
                      step="0.01"
                      label="最小种子体积（GB）"
                      hint="大于此体积才购买使用折扣"
                      persistent-hint
                    />
                  </VCol>
                </VRow>
              </section>
            </VWindowItem>

            <VWindowItem value="selection">
              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">来源与促销</div>
                    <div class="text-body-2 text-medium-emphasis">沿用站点列表页或 RSS 获取链路</div>
                  </div>
                </header>
                <VRow>
                  <VCol cols="12" md="6">
                    <VSelect
                      v-model="localTask.freeleech"
                      label="促销"
                      :items="[
                        { title: '全部（包括普通）', value: '' },
                        { title: '免费', value: 'free' },
                        { title: '2X 免费', value: '2xfree' },
                      ]"
                    />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VSelect
                      v-model="localTask.hr"
                      label="排除 H&R"
                      :items="[
                        { title: '是', value: 'yes' },
                        { title: '否', value: 'no' },
                      ]"
                    />
                  </VCol>
                </VRow>
                <div class="editor-switches">
                  <VSwitch v-model="localTask.rss_support" label="使用 RSS" color="primary" hide-details inset />
                  <VSwitch v-model="localTask.except_subscribe" label="排除订阅" color="primary" hide-details inset />
                  <VSwitch v-model="localTask.site_hr_active" label="全站 H&R" color="primary" hide-details inset />
                  <VSwitch v-model="localTask.resurrect_enabled" label="复活区" color="primary" hide-details inset />
                </div>
                <VRow v-if="localTask.resurrect_enabled">
                  <VCol cols="12">
                    <VTextField
                      v-model="localTask.resurrect_url"
                      label="复活区URL"
                      placeholder="输入自定义列表页链接"
                      :rules="[value => !!String(value || '').trim() || '请输入复活区URL']"
                    />
                  </VCol>
                  <VCol cols="12">
                    <VTextField
                      v-model.number="localTask.resurrect_min_peers"
                      label="最小下载人数"
                      hint="下载人数大于此值才入选"
                      persistent-hint
                    />
                  </VCol>
                </VRow>
              </section>

              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">候选过滤</div>
                    <div class="text-body-2 text-medium-emphasis">范围字段支持单值或“最小值-最大值”</div>
                  </div>
                </header>
                <VRow>
                  <VCol cols="12" md="4">
                    <VTextField v-model="localTask.size" label="种子大小（GB）" placeholder="10-80" />
                  </VCol>
                  <VCol cols="12" md="4">
                    <VTextField v-model="localTask.seeder" label="做种人数" placeholder="1-10" />
                  </VCol>
                  <VCol cols="12" md="4">
                    <VTextField v-model="localTask.timezone_offset" type="number" label="站点时区偏移（小时）" />
                  </VCol>
                </VRow>
                <!-- v5.5.7：新种/老种发布时间过滤，两字段相邻，OR 语义（满足任一即可下载） -->
                <div class="text-caption text-medium-emphasis mb-2">
                  发布时间过滤（新种与老种二选一即可下载）：新种设发布上限（分钟）、老种设发布下限（小时），同时设置时满足任一侧即下载，留空则不限。
                </div>
                <VRow>
                  <VCol cols="12" md="4">
                    <VTextField
                      v-model="localTask.pubtime"
                      label="新种发布时间上限（分钟）"
                      placeholder="如 5-120"
                      hint="发布时间不超过此值才下载"
                      persistent-hint
                    />
                  </VCol>
                  <VCol cols="12" md="4">
                    <VTextField
                      v-model.number="localTask.old_seed_min_age"
                      type="number"
                      min="0.01"
                      step="1"
                      label="老种发布时间下限（小时）"
                      placeholder="如 300"
                      hint="发布时间超过此小时数才下载"
                      persistent-hint
                    />
                  </VCol>
                </VRow>
                <VRow>
                  <VCol cols="12" md="8">
                    <VTextField v-model="localTask.include" label="包含规则" placeholder="支持正则表达式" />
                  </VCol>
                  <VCol cols="12">
                    <VTextField v-model="localTask.exclude" label="排除规则" placeholder="支持正则表达式" />
                  </VCol>
                </VRow>
              </section>
            </VWindowItem>

            <VWindowItem value="limits">
              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">新增任务上限</div>
                    <div class="text-body-2 text-medium-emphasis">达到任一上限后停止为当前任务新增种子</div>
                  </div>
                </header>
                <VRow>
                  <VCol cols="12" md="6">
                    <VTextField v-model.number="localTask.disksize" type="number" min="0" label="保种体积（GB）" />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VTextField v-model.number="localTask.maxdlcount" type="number" min="0" label="同时下载任务数" />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VTextField v-model.number="localTask.maxupspeed" type="number" min="0" label="总上传带宽（MB/s）" />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VTextField v-model.number="localTask.maxdlspeed" type="number" min="0" label="总下载带宽（MB/s）" />
                  </VCol>
                </VRow>
              </section>
              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">单种限速</div>
                    <div class="text-body-2 text-medium-emphasis">只作用于当前任务新添加的种子</div>
                  </div>
                </header>
                <VRow>
                  <VCol cols="12" md="6">
                    <VTextField v-model.number="localTask.up_speed" type="number" min="0" label="上传限速（MB/s）" />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VTextField v-model.number="localTask.dl_speed" type="number" min="0" label="下载限速（MB/s）" />
                  </VCol>
                </VRow>
              </section>
            </VWindowItem>

            <VWindowItem value="speedlimit">
              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">任务级限速</div>
                    <div class="text-body-2 text-medium-emphasis">当分享率达到阈值时自动限速任务种子，检查间隔复用状态检查周期</div>
                  </div>
                </header>
                <VRow>
                  <VCol cols="12" md="6">
                    <VSwitch
                      v-model="localTask.speed_limit_downloading"
                      label="下载中分享率达到即触发"
                      color="primary"
                      hide-details
                      inset
                    />
                    <VRow v-if="localTask.speed_limit_downloading" class="mt-3">
                      <VCol cols="12" md="6">
                        <VTextField
                          v-model.number="localTask.speed_limit_downloading_threshold"
                          type="number"
                          min="0.1"
                          step="0.1"
                          label="下载中分享率阈值"
                          hint="下载中分享率达到此值时触发限速"
                          persistent-hint
                        />
                      </VCol>
                      <VCol cols="12" md="6">
                        <VTextField
                          v-model.number="localTask.speed_limit_downloading_value"
                          type="number"
                          min="1"
                          step="1"
                          label="下载中限速值（KB/s）"
                          hint="下载中触发后限速为此值"
                          persistent-hint
                        />
                      </VCol>
                    </VRow>
                  </VCol>
                  <VCol cols="12" md="6">
                    <VSwitch
                      v-model="localTask.speed_limit_complete"
                      label="下载完成后触发"
                      color="primary"
                      hide-details
                      inset
                    />
                    <VRow v-if="localTask.speed_limit_complete" class="mt-3">
                      <VCol cols="12" md="6">
                        <VTextField
                          v-model.number="localTask.speed_limit_complete_threshold"
                          type="number"
                          min="0.1"
                          step="0.1"
                          label="完成后分享率阈值"
                          hint="完成后分享率达到此值时触发限速"
                          persistent-hint
                        />
                      </VCol>
                      <VCol cols="12" md="6">
                        <VTextField
                          v-model.number="localTask.speed_limit_complete_value"
                          type="number"
                          min="1"
                          step="1"
                          label="完成后限速值（KB/s）"
                          hint="完成后触发后限速为此值"
                          persistent-hint
                        />
                      </VCol>
                    </VRow>
                  </VCol>
                </VRow>
              </section>
            </VWindowItem>

            <VWindowItem value="delete">
              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">
                      {{ globalDynamicDelete ? '全局删种托管' : '删除模式' }}
                    </div>
                    <div class="text-body-2 text-medium-emphasis">
                      {{ globalDynamicDelete ? '选择此任务是否参加全局阈值兜底淘汰' : '动态模式会在超过体积阈值后按现有算法托管删种' }}
                    </div>
                  </div>
                </header>
                <VBtnToggle v-model="localTask.proxy_delete" mandatory color="primary" divided>
                  <VBtn :value="false">{{ globalDynamicDelete ? '不参与托管' : '按条件删除' }}</VBtn>
                  <VBtn :value="true">{{ globalDynamicDelete ? '参与全局托管' : '动态删种' }}</VBtn>
                </VBtnToggle>
                <VRow v-if="localTask.proxy_delete && !globalDynamicDelete">
                  <VCol cols="12">
                    <VTextField
                      v-model="localTask.delete_size_range"
                      label="动态删种阈值（GB）"
                      placeholder="如 350-500"
                    />
                  </VCol>
                </VRow>
              </section>
              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">触发条件</div>
                    <div class="text-body-2 text-medium-emphasis">普通模式满足任一条件即删除</div>
                  </div>
                </header>
                <VRow>
                  <VCol cols="12" md="4">
                    <VTextField v-model.number="localTask.seed_time" type="number" min="0" label="做种时间（小时）" />
                  </VCol>
                  <VCol cols="12" md="4">
                    <VTextField v-model.number="localTask.hr_seed_time" type="number" min="0" label="H&R 做种时间（小时）" />
                  </VCol>
                  <VCol cols="12" md="4">
                    <VTextField v-model.number="localTask.seed_ratio" type="number" min="0" label="分享率" />
                  </VCol>
                  <VCol cols="12" md="4">
                    <VTextField v-model.number="localTask.seed_size" type="number" min="0" label="上传量（GB）" />
                  </VCol>
                  <VCol cols="12" md="4">
                    <VTextField v-model.number="localTask.download_time" type="number" min="0" label="下载超时（小时）" />
                  </VCol>
                  <VCol cols="12" md="4">
                    <VTextField v-model.number="localTask.seed_inactivetime" type="number" min="0" label="未活动时间（分钟）" />
                  </VCol>
                  <VCol cols="12" md="4">
                    <VTextField v-model.number="localTask.seed_avgspeed" type="number" min="0" label="上传速度阈值（KB/s）" />
                  </VCol>
                  <VCol cols="12" md="8">
                    <VTextField v-model="localTask.delete_except_tags" label="删除排除标签" />
                  </VCol>
                </VRow>
                <VSwitch
                  v-model="localTask.del_no_free"
                  label="删除促销过期的未完成下载"
                  color="primary"
                  hide-details
                  inset
                />
                <!-- v5.3.0：只删除已完成种子 -->
                <VSwitch
                  v-model="localTask.only_delete_completed"
                  label="只删除已完成种子"
                  color="primary"
                  hide-details
                  inset
                />
                <!-- v5.3.0：最小做种时间（分钟） -->
                <VRow v-show="localTask.only_delete_completed">
                  <VCol cols="12" md="4">
                    <VTextField
                      v-model.number="localTask.min_seed_time"
                      type="number"
                      min="0"
                      label="最小做种时间（分钟）"
                    />
                  </VCol>
                </VRow>
              </section>
            </VWindowItem>

            <VWindowItem value="advanced">
              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">下载器适配</div>
                    <div class="text-body-2 text-medium-emphasis">保留原有分类、提示跳过和自动归档能力</div>
                  </div>
                </header>
                <VRow>
                  <VCol cols="12" md="6">
                    <VTextField v-model="localTask.qb_category" label="qBittorrent 分类" />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VTextField v-model.number="localTask.auto_archive_days" type="number" min="0" label="自动归档天数" />
                  </VCol>
                </VRow>
                <div class="editor-switches">
                  <VSwitch v-model="localTask.site_skip_tips" label="自动跳过下载提示" color="primary" hide-details inset />
                </div>
                <VDivider />
                <VSwitch
                  v-model="localTask.site_data_enabled"
                  label="站点数据自动刷新"
                  color="primary"
                  hide-details
                  inset
                />
                <VTextField
                  v-if="localTask.site_data_enabled"
                  v-model.number="localTask.site_data_interval"
                  type="number"
                  min="1"
                  label="站点数据刷新间隔（分钟）"
                  placeholder="30"
                  clearable
                  hide-details
                />
              </section>
            </VWindowItem>
          </VWindow>

          <VSheet tag="aside" class="brushflow-editor__summary">
            <div class="text-subtitle-1 font-weight-medium">配置摘要</div>
            <dl>
              <div><dt>站点</dt><dd>{{ siteName }}</dd></div>
              <div><dt>下载器</dt><dd>{{ localTask.downloader || '未选择' }}</dd></div>
              <div><dt>刷新</dt><dd>{{ scheduleText }}</dd></div>
              <div><dt>检查</dt><dd>每 {{ localTask.check_interval || 5 }} 分钟</dd></div>
              <div><dt>时段</dt><dd>{{ localTask.active_time_range || '全天' }}</dd></div>
              <div><dt>目标分享率</dt><dd>{{ localTask.site_ratio_control ? localTask.site_ratio_target || '未设置' : '关闭' }}</dd></div>
              <div><dt>下载量限制</dt><dd>{{ localTask.download_limit_enabled ? `${localTask.download_limit} GB` : '关闭' }}</dd></div>
              <div><dt>促销</dt><dd>{{ localTask.freeleech === '2xfree' ? '2X 免费' : localTask.freeleech === 'free' ? '免费' : '全部' }}</dd></div>
              <div><dt>保种上限</dt><dd>{{ localTask.disksize ? `${localTask.disksize} GB` : '不限' }}</dd></div>
              <div><dt>删种规则</dt><dd>{{ localTask.proxy_delete ? '动态删种' : '满足任一条件' }}{{ localTask.only_delete_completed ? '(只删除已完成种子)' : '' }}</dd></div>
            </dl>
          </VSheet>
        </VForm>
      </VCardText>
    </VCard>
  </VDialog>
</template>

<style scoped>
.brushflow-editor {
  max-block-size: min(90dvh, 58rem);
}

.brushflow-editor__toolbar {
  flex: 0 0 auto;
  padding-inline: 8px;
  z-index: 4;
  backdrop-filter: blur(var(--transparent-blur, 0px));
  background-color: rgba(var(--v-theme-surface), var(--transparent-opacity-heavy, 1));
}

.brushflow-editor__body {
  padding: 0;
}

.brushflow-editor__form {
  display: grid;
  grid-template-columns: 12rem auto minmax(0, 1fr) minmax(12rem, 0.34fr);
  min-block-size: 34rem;
}

.brushflow-editor__tabs {
  padding: 12px 8px;
}

.brushflow-editor__window {
  min-inline-size: 0;
  padding: 20px;
}

.editor-section {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.editor-section + .editor-section {
  margin-block-start: 28px;
  padding-block-start: 24px;
  border-block-start: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}

.editor-section__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.editor-switches {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 24px;
}

.brushflow-editor__summary {
  padding: 20px 16px;
  border-inline-start: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}

.brushflow-editor__summary dl {
  display: grid;
  gap: 12px;
  margin: 18px 0 0;
}

.brushflow-editor__summary dl > div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.brushflow-editor__summary dt {
  color: rgba(var(--v-theme-on-surface), var(--v-medium-emphasis-opacity));
}

.brushflow-editor__summary dd {
  margin: 0;
  text-align: end;
  overflow-wrap: anywhere;
}

@media (max-width: 959px) {
  .brushflow-editor {
    max-block-size: none;
  }

  .brushflow-editor__form {
    grid-template-columns: 1fr;
    min-block-size: 0;
  }

  .brushflow-editor__tabs {
    max-inline-size: 100%;
    padding-block: 0;
    overflow-x: auto;
  }

  .brushflow-editor__window {
    padding: 16px;
  }

  .brushflow-editor__summary {
    border-inline-start: 0;
    border-block-start: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  }
}

@media (max-width: 599px) {
  .brushflow-editor__toolbar :deep(.v-toolbar-title) {
    font-size: 1rem;
  }

  .brushflow-editor__toolbar :deep(.v-btn__content) {
    white-space: normal;
  }

}
</style>
