"""订阅合同状态常量（doc94 C1/F2）。

单独成文件而不是散在各 service 里，是因为「哪些状态算此刻仍在生效」这件事
一旦两处不一致，就会出现「用户取消续费后再买一份，同时持有两个可用订阅池」这类错。
"""

#: 「取消自动续费」：合同照常生效到期末，只是不再创建续费订单。
STATUS_CANCEL_AT_PERIOD_END = 'cancel_at_period_end'

#: 「此刻仍在生效」的合同状态集合。取消自动续费的合同**仍然生效**。
CURRENT_CONTRACT_STATUSES = ('active', STATUS_CANCEL_AT_PERIOD_END)
