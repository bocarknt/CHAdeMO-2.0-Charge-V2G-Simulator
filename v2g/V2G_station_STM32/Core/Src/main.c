/* USER CODE BEGIN Header */
/**
 ******************************************************************************
 * @file    main.c
 * @brief   Station MC — CHAdeMO 2.0 V2G
 *          Pont UART (station sim) ↔ CAN (battery MC)
 *
 * Phase 0 : rx CAN  0x100 → TX UART MSG100
 *           rx UART MSG108 → TX CAN 0x108
 *           rx CAN  0x101 → TX UART MSG101
 *           rx UART MSG109 → TX CAN 0x109
 *
 * Phase 1 : rx CAN  0x200 → TX UART MSG200
 *           rx UART MSG208 → TX CAN 0x208
 *
 * Phase 2 : rx CAN  0x201 → TX UART MSG201
 *           rx UART MSG209 → TX CAN 0x209
 *
 * Phase 3 (boucle 100ms) :
 *           rx CAN  0x102 → TX UART MSG102
 *           rx CAN  0x200 → TX UART MSG200 (non-bloquant)
 *           rx UART MSG109 → TX CAN 0x109  (non-bloquant)
 *           rx UART MSG208 → TX CAN 0x208  (non-bloquant)
 *
 * Phase 4 : arrêt propre → STATE:IDLE → reboucle while(1)
 ******************************************************************************
 */
/* USER CODE END Header */

#include "main.h"
#include "can.h"
#include "usart.h"
#include "gpio.h"
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */

/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */
uint16_t evse_available_voltage    = 400;
uint16_t evse_output_voltage_max   = 500;
uint16_t evse_output_voltage_min   = 200;
uint8_t  evse_available_current    = 100;
uint16_t evse_threshold_voltage    = 380;
uint8_t  evse_welding_detection    = 1;
uint8_t  evse_protocol_number      = 0x03;
uint16_t evse_present_voltage      = 0;
uint8_t  evse_present_current      = 0;
uint8_t  evse_stop_control         = 1;
uint8_t  evse_system_error         = 0;
uint8_t  evse_battery_incompat     = 0;
uint8_t  evse_connector_lock       = 0;
uint8_t  evse_error                = 0;
uint8_t  evse_status               = 0;
uint8_t  discharge_compatible      = 1;
uint8_t  evse_present_discharge_i  = 0xFF;
uint16_t evse_input_voltage        = 400;
uint8_t  evse_input_current        = 0xFF;
uint16_t evse_lower_threshold_v    = 280;
uint8_t  evse_sequence_number      = 0x01;
uint16_t evse_remaining_dis_t      = 0;

uint16_t veh_min_charge_i   = 0;
uint16_t veh_min_voltage    = 0;
uint16_t veh_max_voltage    = 0;
uint8_t  veh_soc            = 0;
uint16_t veh_target_voltage = 0;
uint8_t  veh_max_charge_i   = 0;
uint8_t  veh_charging_en    = 0;
uint8_t  veh_stop           = 0;
uint8_t  veh_max_dis_i      = 0;
uint16_t veh_min_dis_v      = 0;
uint8_t  veh_min_soc        = 0;
uint8_t  veh_seq_num        = 0;
uint16_t veh_dis_time       = 0;
uint16_t veh_energy         = 0;

volatile uint8_t flag_msg_100 = 0;
volatile uint8_t flag_msg_101 = 0;
volatile uint8_t flag_msg_102 = 0;
volatile uint8_t flag_msg_200 = 0;
volatile uint8_t flag_msg_201 = 0;
uint8_t rxData_100[8];
uint8_t rxData_101[8];
uint8_t rxData_102[8];
uint8_t rxData_200[8];
uint8_t rxData_201[8];

uint8_t txbuf[256];
int     Cable_Detected = 0;

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
#define RX_BUF_SIZE 128

static uint8_t          rx_byte;
static char             rx_buf[RX_BUF_SIZE];
static uint16_t         rx_idx        = 0;
static char             rx_line[RX_BUF_SIZE];
static volatile uint8_t rx_line_ready = 0;

void UART_RX_Start(void)
{
    rx_idx = 0; rx_line_ready = 0;
    HAL_UART_Receive_IT(&huart2, &rx_byte, 1);
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance != USART2) return;
    char c = (char)rx_byte;
    if (c == '\n') {
        if (rx_idx > 0 && rx_buf[rx_idx - 1] == '\r') rx_idx--;
        rx_buf[rx_idx] = '\0';
        strncpy(rx_line, rx_buf, RX_BUF_SIZE - 1);
        rx_line[RX_BUF_SIZE - 1] = '\0';
        rx_idx = 0; rx_line_ready = 1;
    } else {
        if (rx_idx < RX_BUF_SIZE - 1) rx_buf[rx_idx++] = c;
    }
    HAL_UART_Receive_IT(&huart2, &rx_byte, 1);
}

void UART_Send(const char *msg)
{
    HAL_UART_Transmit(&huart2, (uint8_t *)msg, strlen(msg), 100);
}

static int get_field(const char *line, int index)
{
    const char *p = strchr(line, ':');
    if (!p) return 0; p++;
    for (int i = 0; i < index; i++) {
        p = strchr(p, ',');
        if (!p) return 0; p++;
    }
    return atoi(p);
}

/**
 * @brief Attend une ligne UART. Ignore les DBG: et gère le RESET.
 */
static void wait_for_line(char *out)
{
    do {
        while (!rx_line_ready) { HAL_Delay(1); }
        __disable_irq();
        strncpy(out, rx_line, RX_BUF_SIZE - 1);
        out[RX_BUF_SIZE - 1] = '\0';
        rx_line_ready = 0;
        __enable_irq();
        if (strncmp(out, "RESET", 5) == 0) {
            uint8_t d[1] = {0xFF};
            CAN_TxHeaderTypeDef h; uint32_t mb;
            h.StdId = 0x7FF; h.IDE = CAN_ID_STD; h.RTR = CAN_RTR_DATA; h.DLC = 1;
            HAL_CAN_AddTxMessage(&hcan1, &h, d, &mb);
            HAL_Delay(10); HAL_NVIC_SystemReset();
        }
    } while (strncmp(out, "DBG:", 4) == 0);
}

static void CAN_Send(uint32_t id, uint8_t *data, uint8_t len)
{
    CAN_TxHeaderTypeDef h; uint32_t mb;
    h.StdId = id; h.IDE = CAN_ID_STD; h.RTR = CAN_RTR_DATA; h.DLC = len;
    if (HAL_CAN_AddTxMessage(&hcan1, &h, data, &mb) != HAL_OK)
        UART_Send("STATE:CAN_ERROR\r\n");
}

/* ── TX CAN ────────────────────────────────────────────────────────────────── */

static void send_0x108(void)
{
    uint8_t d[8] = {0};
    d[0] = evse_welding_detection;
    d[1] = evse_available_voltage & 0xFF; d[2] = evse_available_voltage >> 8;
    d[3] = evse_available_current;
    d[4] = evse_threshold_voltage & 0xFF; d[5] = evse_threshold_voltage >> 8;
    CAN_Send(0x108, d, 8);
}

static void send_0x109(uint8_t rem_10s, uint8_t rem_1min)
{
    uint8_t d[8] = {0};
    d[0] = evse_protocol_number;
    d[1] = evse_present_voltage & 0xFF; d[2] = evse_present_voltage >> 8;
    d[3] = evse_present_current;
    d[4] = discharge_compatible & 0x01;
    d[5] = (evse_stop_control     << 5) | (evse_system_error   << 4) |
           (evse_battery_incompat << 3) | (evse_connector_lock << 2) |
           (evse_error            << 1) | (evse_status         << 0);
    d[6] = rem_10s;
    d[7] = rem_1min;
    CAN_Send(0x109, d, 8);
}

static void send_0x208(void)
{
    uint8_t d[8] = {0};
    d[0] = evse_present_discharge_i;
    d[1] = evse_input_voltage & 0xFF; d[2] = evse_input_voltage >> 8;
    d[3] = evse_input_current;
    d[6] = evse_lower_threshold_v & 0xFF; d[7] = evse_lower_threshold_v >> 8;
    CAN_Send(0x208, d, 8);
}

static void send_0x209(void)
{
    uint8_t d[8] = {0};
    d[0] = evse_sequence_number;
    d[1] = evse_remaining_dis_t & 0xFF; d[2] = evse_remaining_dis_t >> 8;
    CAN_Send(0x209, d, 8);
}

/* ── RX CAN → forward UART ─────────────────────────────────────────────────── */

static void process_0x100(void)
{
    if (!flag_msg_100) return; flag_msg_100 = 0;
    veh_min_charge_i = rxData_100[0];
    veh_min_voltage  = rxData_100[2] | (rxData_100[3] << 8);
    veh_max_voltage  = rxData_100[4] | (rxData_100[5] << 8);
    veh_soc          = rxData_100[6];
    if (veh_max_voltage <= evse_output_voltage_max &&
        veh_max_voltage >= evse_output_voltage_min) {
        evse_available_voltage = veh_max_voltage;
        evse_battery_incompat  = 0;
    } else {
        evse_battery_incompat  = 1;
    }
    evse_threshold_voltage = evse_available_voltage - 20;
    sprintf((char *)txbuf, "MSG100:%d,%d,%d,%d\r\n",
            veh_min_charge_i, veh_min_voltage, veh_max_voltage, veh_soc);
    UART_Send((char *)txbuf);
}

static void process_0x101(void)
{
    if (!flag_msg_101) return; flag_msg_101 = 0;
    uint16_t cap = rxData_101[5] | (rxData_101[6] << 8);
    sprintf((char *)txbuf, "MSG101:%d,%d,%d,%d\r\n",
            rxData_101[1], rxData_101[2], rxData_101[3], cap);
    UART_Send((char *)txbuf);
}

static void process_0x102(void)
{
    if (!flag_msg_102) return; flag_msg_102 = 0;
    veh_target_voltage = rxData_102[1] | (rxData_102[2] << 8);
    veh_max_charge_i   = rxData_102[3];
    veh_charging_en    = rxData_102[5] & 0x01;
    veh_stop           = (rxData_102[5] >> 4) & 0x01;
    veh_soc            = rxData_102[6];
    sprintf((char *)txbuf, "MSG102:%d,%d,%d,%d,%d\r\n",
            veh_target_voltage, veh_max_charge_i, veh_soc, veh_charging_en, veh_stop);
    UART_Send((char *)txbuf);
}

static void process_0x200(void)
{
    if (!flag_msg_200) return; flag_msg_200 = 0;
    veh_max_dis_i = 0xFF - rxData_200[0];
    veh_min_dis_v = rxData_200[4] | (rxData_200[5] << 8);
    veh_min_soc   = rxData_200[6];
    sprintf((char *)txbuf, "MSG200:%d,%d,%d,%d\r\n",
            veh_max_dis_i, veh_min_dis_v, veh_min_soc, rxData_200[7]);
    UART_Send((char *)txbuf);
}

static void process_0x201(void)
{
    if (!flag_msg_201) return; flag_msg_201 = 0;
    veh_seq_num  = rxData_201[0];
    veh_dis_time = rxData_201[1] | (rxData_201[2] << 8);
    veh_energy   = rxData_201[3] | (rxData_201[4] << 8);
    sprintf((char *)txbuf, "MSG201:%d,%d,%d\r\n", veh_seq_num, veh_dis_time, veh_energy);
    UART_Send((char *)txbuf);
}

/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
/* ── main ──────────────────────────────────────────────────────────────────── */

int main(void)
{
    HAL_Init();
    SystemClock_Config();
    MX_GPIO_Init();
    MX_USART2_UART_Init();
    MX_CAN1_Init();

    CAN_Filter_Config();
    HAL_CAN_Start(&hcan1);
    HAL_CAN_ActivateNotification(&hcan1, CAN_IT_RX_FIFO0_MSG_PENDING);
    UART_RX_Start();

    char line[RX_BUF_SIZE];

    while (1)
    {
        evse_stop_control = 1;
        evse_status       = 0;

        /* ── Phase 0 — Handshake ─────────────────────────────────────────── */

        /* 1. Attente CAN 0x100 → TX UART MSG100 */
        while (!flag_msg_100) { HAL_Delay(1); }
        process_0x100();

        /* 2. Attente MSG108 UART → TX CAN 0x108
              station sim clique Phase 0 après réception MSG100 */
        wait_for_line(line);
        if (strncmp(line, "MSG108:", 7) == 0) {
            evse_welding_detection = get_field(line, 0);
            evse_available_voltage = get_field(line, 1);
            evse_available_current = get_field(line, 2);
            evse_threshold_voltage = get_field(line, 3);
            send_0x108();
        }

        /* 3. Attente CAN 0x101 → TX UART MSG101
              station sim envoie MSG109 automatiquement après MSG101 */
        while (!flag_msg_101) { HAL_Delay(1); }
        process_0x101();

        /* 4. Attente MSG109 UART → TX CAN 0x109 */
        wait_for_line(line);
        if (strncmp(line, "MSG109:", 7) == 0) {
            evse_present_voltage = get_field(line, 0);
            evse_present_current = get_field(line, 1);
            evse_stop_control    = get_field(line, 2);
            discharge_compatible = get_field(line, 3);
            uint8_t rem_min      = (uint8_t)get_field(line, 4);
            send_0x109(0xFF, rem_min);
        }

        /* ── Phase 1 — Négociation V2G ───────────────────────────────────── */

        /* 5. Attente CAN 0x200 → TX UART MSG200 */
        while (!flag_msg_200) { HAL_Delay(1); }
        process_0x200();

        /* 6. Attente MSG208 UART → TX CAN 0x208 */
        wait_for_line(line);
        if (strncmp(line, "MSG208:", 7) == 0) {
            evse_present_discharge_i = get_field(line, 0);
            evse_input_voltage       = get_field(line, 1);
            evse_input_current       = get_field(line, 2);
            evse_lower_threshold_v   = get_field(line, 3);
            send_0x208();
        }

        /* ── Phase 2 — Accord séquence ───────────────────────────────────── */

        /* 7. Attente CAN 0x201 → TX UART MSG201 */
        while (!flag_msg_201) { HAL_Delay(1); }
        process_0x201();

        if (veh_seq_num != evse_sequence_number) {
            UART_Send("STATE:SEQ_MISMATCH\r\n");
            continue;
        }

        /* 8. Attente MSG209 UART → TX CAN 0x209 */
        wait_for_line(line);
        if (strncmp(line, "MSG209:", 7) == 0) {
            evse_sequence_number = get_field(line, 0);
            evse_remaining_dis_t = get_field(line, 1);
            send_0x209();
        }

        /* ── Phase 3 — Boucle décharge V2G (100ms) ──────────────────────── */
        UART_Send("STATE:DISCHARGING\r\n");
        evse_stop_control = 0;
        evse_status       = 1;

        do {
            /* 9a. Attente CAN 0x102 → TX UART MSG102
                  bloquant : on attend le prochain tick du battery sim */
            while (!flag_msg_102) { HAL_Delay(1); }
            process_0x102();

            /* 9b. CAN 0x200 non-bloquant → TX UART MSG200
                  (battery sim envoie MSG200 juste après MSG102) */
            if (flag_msg_200) {
                process_0x200();
            }

            /* 9c. MSG109 UART non-bloquant → TX CAN 0x109
                  Le station sim répond automatiquement à MSG102
                  quand Phase 3 est active. Pas bloquant car le premier
                  MSG102 arrive avant que Phase 3 soit cliquée. */
            if (rx_line_ready) {
                __disable_irq();
                strncpy(line, rx_line, RX_BUF_SIZE - 1);
                line[RX_BUF_SIZE - 1] = '\0';
                rx_line_ready = 0;
                __enable_irq();

                if (strncmp(line, "MSG109:", 7) == 0) {
                    evse_present_voltage = get_field(line, 0);
                    evse_present_current = get_field(line, 1);
                    evse_stop_control    = get_field(line, 2);
                    uint8_t rem          = (uint8_t)get_field(line, 4);
                    evse_remaining_dis_t = rem;
                    send_0x109(0xFF, rem);
                }
                /* 9d. MSG208 peut arriver juste après MSG109 */
                else if (strncmp(line, "MSG208:", 7) == 0) {
                    evse_present_discharge_i = get_field(line, 0);
                    evse_input_voltage       = get_field(line, 1);
                    evse_input_current       = get_field(line, 2);
                    send_0x208();
                }
            }

            /* Conditions d'arrêt */
            if (veh_stop || !veh_charging_en) {
                UART_Send("STATE:VEHICLE_STOP\r\n");
                evse_stop_control = 1;
                break;
            }
            if (veh_soc <= veh_min_soc) {
                UART_Send("STATE:SOC_MIN\r\n");
                evse_stop_control = 1;
                break;
            }
            if (evse_stop_control) {
                UART_Send("STATE:EVSE_STOP\r\n");
                break;
            }

        } while (1);

        /* ── Phase 4 — Arrêt propre ──────────────────────────────────────── */
        UART_Send("STATE:STOPPING\r\n");
        evse_stop_control        = 1;
        evse_status              = 0;
        evse_present_discharge_i = 0xFF;
        evse_present_current     = 0;
        evse_present_voltage     = 0;
        send_0x109(0x00, 0x00);
        send_0x208();
        HAL_Delay(500);
        UART_Send("STATE:IDLE\r\n");
    }
}


/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  if (HAL_PWREx_ControlVoltageScaling(PWR_REGULATOR_VOLTAGE_SCALE1) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSI;
  RCC_OscInitStruct.PLL.PLLM = 1;
  RCC_OscInitStruct.PLL.PLLN = 10;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV7;
  RCC_OscInitStruct.PLL.PLLQ = RCC_PLLQ_DIV2;
  RCC_OscInitStruct.PLL.PLLR = RCC_PLLR_DIV2;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV1;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_4) != HAL_OK)
  {
    Error_Handler();
  }
}

/* USER CODE BEGIN 4 */

void CAN_Filter_Config(void)
{
    CAN_FilterTypeDef f;
    f.FilterActivation     = ENABLE;
    f.FilterBank           = 0;
    f.FilterFIFOAssignment = CAN_FILTER_FIFO0;
    f.FilterMode           = CAN_FILTERMODE_IDMASK;
    f.FilterScale          = CAN_FILTERSCALE_32BIT;
    f.FilterIdHigh = f.FilterIdLow = 0x0000;
    f.FilterMaskIdHigh = f.FilterMaskIdLow = 0x0000;
    HAL_CAN_ConfigFilter(&hcan1, &f);
}

void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin)
{
    Cable_Detected = 1;
}

void HAL_CAN_RxFifo0MsgPendingCallback(CAN_HandleTypeDef *hcan)
{
    CAN_RxHeaderTypeDef rxh;
    uint8_t tmp[8];
    if (HAL_CAN_GetRxMessage(hcan, CAN_RX_FIFO0, &rxh, tmp) != HAL_OK) return;
    switch (rxh.StdId) {
        case 0x100: memcpy(rxData_100, tmp, 8); flag_msg_100 = 1; break;
        case 0x101: memcpy(rxData_101, tmp, 8); flag_msg_101 = 1; break;
        case 0x102: memcpy(rxData_102, tmp, 8); flag_msg_102 = 1; break;
        case 0x200: memcpy(rxData_200, tmp, 8); flag_msg_200 = 1; break;
        case 0x201: memcpy(rxData_201, tmp, 8); flag_msg_201 = 1; break;
        case 0x7FF:
            UART_Send("RESET\r\n");
            HAL_Delay(10);
            HAL_NVIC_SystemReset();
            break;
    }
}

/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
