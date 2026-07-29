# calc_rmr.R — OxyViewer R 接口
# 用于 respR 批量计算耗氧率，可从 Python 通过 Rscript 调用
#
# 用法:
#   Rscript calc_rmr.R <data_folder> <params_csv> <meas_time> <channels>
#
#   参数:
#     data_folder : 数据文件夹 (如 "I:/Rtools/20260422/20260422 20")
#     params_csv  : 参数文件路径 (如 "I:/Rtools/20260422/raw/meas_params.csv")
#     meas_time   : 实验日期 (如 20260422)
#     channels    : 要计算的通道号, 逗号分隔 (如 "1,2,3" 或 "1" 或 "1-9")
#
# 示例:
#   Rscript calc_rmr.R "I:/Rtools/20260422/20260422 20" "I:/Rtools/20260422/raw/meas_params.csv" 20260422 "1-9"

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 4) {
  stop("Usage: Rscript calc_rmr.R <data_folder> <params_csv> <meas_time> <channels>")
}

library(respR)
library(lubridate)
library(readxl)

# 禁止生成 rplot.pdf
pdf(NULL)

# ── 参数解析 ──
data_folder <- args[1]
params_file <- args[2]
meas_time    <- as.integer(args[3])
ch_str       <- args[4]

# 解析通道列表 (支持 "1,3,5" 或 "2-9" 或 "1")
if (grepl("-", ch_str)) {
  parts <- strsplit(ch_str, "-")[[1]]
  channel <- as.integer(parts[1]):as.integer(parts[2])
} else {
  channel <- as.integer(strsplit(ch_str, ",")[[1]])
}

# ── 渗透系数矩阵 ──
k <- matrix(data = NA, nrow = 9, ncol = 2)
colnames(k) <- c("channel", "k_value")
k[, "channel"] <- 1:9
k[, "k_value"] <- c(0.0006223, 0.000317161, 0.001055724, 0.000671915,
                    0.000537423, 0.001256691, 0.000743536, 0.000743536, 0.000743536)

# ── 读取循环参数 ──
if (!file.exists(params_file)) {
  stop("循环参数文件未找到: ", params_file)
}
params <- read.csv(params_file, fileEncoding = "UTF-8-BOM")

# ── 为每个通道匹配参数并计算 ──
for (x in channel) {

  # 确定 rmr_type
  if (x == 1) {
    rmr_type <- "blank"
  } else {
    rmr_type <- "fish"
  }

  # 从 chamber_ID 匹配参数行
  mode_params <- NULL
  for (i in seq_len(nrow(params))) {
    if (params$meas_time[i] != meas_time) next
    if (params$rmr_type[i] != rmr_type) next
    # 检查 chamber_ID 是否包含此通道
    cid <- as.character(params$chamber_ID[i])
    ids <- as.integer(unlist(strsplit(gsub("\"", "", cid), ",")))
    if (x %in% ids) {
      mode_params <- i
      break
    }
  }

  if (is.null(mode_params) || length(mode_params) == 0) {
    message("通道 ", x, " 无匹配参数，跳过")
    next
  }

  cycles       <- params$cycles[mode_params]
  cycle_length <- params$cycle_length[mode_params]
  initial      <- params$initial[mode_params]
  cycle_start  <- params$cycle_start[mode_params]
  cycle_time   <- params$cycle_time[mode_params] - params$cycle_start[mode_params] - 5

  if (is.na(cycles) || cycles == 0) {
    message("通道 ", x, " 参数为空，跳过")
    next
  }

  # 循环时间矩阵
  cycle_mat <- matrix(data = NA, nrow = cycles, ncol = 2)
  colnames(cycle_mat) <- c("start", "end")
  cycle_mat[, "start"] <- seq(from = cycle_start + initial, by = cycle_length, length.out = cycles)
  cycle_mat[, "end"]   <- cycle_mat[, "start"] + cycle_time

  # ── 读取数据 ──
  infile  <- file.path(data_folder, paste0(x, ".xlsx"))
  outfile <- file.path(paste0("rmr", x, ".csv"))  # 写入当前工作目录（即导出文件夹）

  if (!file.exists(infile)) {
    message("文件不存在: ", infile, "，跳过")
    next
  }

  df   <- read_excel(path = infile, sheet = 6)
  data <- df[, c(2, 7)]
  names(data) <- c("Date", "Oxygen")

  # 时间格式
  dtm <- parse_date_time(unlist(data$Date), "ymdHMS")
  data$Date <- format(dtm, "%y/%m/%d %H:%M:%S")
  data <- format_time(data, format = "ymdHMS")

  # 渗透系数校正
  k_value <- k[k[, "channel"] == x, "k_value"]
  max_oxy <- mean(df$Oxygen[order(df$Oxygen, decreasing = TRUE)[1:min(30, nrow(df))]],
                  na.rm = TRUE)
  data$oxy <- data$Oxygen - k_value * (max_oxy - data$Oxygen)

  # ── 计算耗氧率 ──
  dataint <- inspect(data, time = 3, oxygen = 4)
  rates <- calc_rate(dataint, from = cycle_mat[, "start"], to = cycle_mat[, "end"], by = "time")
  s <- rates$summary[, 2:13]
  s$rate <- s$rate * 3600  # 转换为 mgO2·L⁻¹·h⁻¹

  # ── 导出 ──
  write.table(x = s, file = outfile, sep = ",", row.names = FALSE, col.names = TRUE)
  message("已保存: ", outfile)
}
