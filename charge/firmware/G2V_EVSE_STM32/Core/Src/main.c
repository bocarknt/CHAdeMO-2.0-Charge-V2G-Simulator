/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "can.h"
#include "usart.h"
#include "gpio.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <string.h>
#include <stdio.h>
#include<stdlib.h>
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
uint8_t rxData[8];
uint8_t rxbuf[128]="\r\n";
uint8_t txbuf[128]="\r\n";
//char rxAffiche[128]="12345678";
// differentes états
typedef enum
{
    EV_STATE_IDLE = 0,
    EV_STATE_CABLE_DETECTED,
    EV_STATE_HANDSHAKE,
    EV_STATE_PRECHARGE,
    EV_STATE_CHARGING,
    EV_STATE_STOP

} EV_State_t;
typedef enum
{
	ON,
	OFF
}etat;// etat des signaux

EV_State_t ev_state = EV_STATE_IDLE;

//Parametre de la voiture
uint16_t battery_voltage_min;      // V
uint16_t battery_voltage_max;      // V
uint16_t battery_voltage;      // V
uint16_t requested_current_max; // A
uint16_t requested_current;      // A
uint8_t soc;               // %

//Parametre de la borne
uint16_t Available_output_voltage_min  = 0;      // V
uint16_t Available_output_voltage_max = 600;      // V
uint16_t Available_output_voltage;      // V
uint16_t Threshold_voltage;  //V
uint16_t Available_output_current_min = 0;      // A
uint16_t Available_output_current_max =200;
uint16_t Available_output_current=0;  //
uint16_t Present_output_voltage;
uint16_t Present_output_current;

uint8_t charger_ready = 0;
uint8_t Battery_incompatibility =0;// 0: compatible, 1: incompatible
uint8_t Welding_detection =1;
uint8_t Charging_stop_control = 1; // 0: operating, 1: during stop control or stop condition
uint8_t Charging_system_error = 0; // 0: normal, 1: error
uint8_t Energizing_state = 0;      // 0: disable, 1: enable
uint8_t Charger_error = 0;         // 0: normal, 1: error
uint8_t Charger_status = 0;		   // 0: standby, 1: charging
uint8_t Remaining_charging_time =0; // in the unit of 1 min
int Cable_Detected;
int test_108=0;

//
uint8_t Maximum_charging_time;
uint8_t Estimated_charging_time;
uint16_t Total_capacity_of_battery;
uint16_t Target_battery_voltage;

uint8_t Battery_voltage_deviation_error =0;
uint8_t High_battery_temperature =0;
uint8_t Battery_current_deviation_error = 0;
uint8_t Battery_undervoltage =0;
uint8_t Battery_overvoltage =0;

uint8_t Normal_stop_request=0;
uint8_t Vehicle_status=0;

uint8_t Vehicle_shift_position=1;
uint8_t Vehicle_charging_enabled=0;




// --- Ajout des flags et buffers pour le traitement hors ISR ---
volatile uint8_t flag_msg_100 = 0;
volatile uint8_t flag_msg_101 = 0;
volatile uint8_t flag_msg_102 = 0;
uint8_t rxData_100[8];
uint8_t rxData_101[8];
uint8_t rxData_102[8];
volatile int stop=0;
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */
void CAN_Filter_Config(void);
void HAL_CAN_RxFifo0MsgPendingCallback(CAN_HandleTypeDef *hcan);
void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin);
void EV_StateMachine(void);
void send_id_108(void);
void send_id_109(void);
void traitement_msg_100(void);
void traitement_msg_101(void);
void traitement_msg_102(void);

//
static void CAN_Send_Frame(uint32_t id, uint8_t *data, uint8_t len);

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
/* ----------------------------------------------------------
   STATION CAPABILITY LIMITS
   Battery MC checks MSG108 against these
   ---------------------------------------------------------- */
#define STATION_MAX_VOLTAGE   420
#define STATION_MIN_VOLTAGE   200
#define STATION_MAX_CURRENT   100

/* ----------------------------------------------------------
   UART RX — interrupt driven
   ---------------------------------------------------------- */
#define RX_BUF_SIZE 128

static uint8_t          rx_byte;
static char             rx_buf[RX_BUF_SIZE];
static uint16_t         rx_idx        = 0;
static char             rx_line[RX_BUF_SIZE];
static volatile uint8_t rx_line_ready = 0;

void UART_RX_Start(void)
{
    rx_idx        = 0;
    rx_line_ready = 0;
    HAL_UART_Receive_IT(&huart2, &rx_byte, 1);
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance != USART2) return;

    char c = (char)rx_byte;

    if (c == '\n')
    {
        if (rx_idx > 0 && rx_buf[rx_idx - 1] == '\r')
            rx_idx--;
        rx_buf[rx_idx] = '\0';
        strncpy(rx_line, rx_buf, RX_BUF_SIZE - 1);
        rx_line[RX_BUF_SIZE - 1] = '\0';
        rx_idx        = 0;
        rx_line_ready = 1;
    }
    else
    {
        if (rx_idx < RX_BUF_SIZE - 1)
            rx_buf[rx_idx++] = c;
    }

    HAL_UART_Receive_IT(&huart2, &rx_byte, 1);
}

/* ----------------------------------------------------------
   TX helper
   ---------------------------------------------------------- */
void UART_Send(const char *msg)
{
    HAL_UART_Transmit(&huart2, (uint8_t *)msg, strlen(msg), 100);
}

/* ----------------------------------------------------------
   Parser helper — returns the Nth comma-separated integer
   after the ':' in a message like "MSG100:5,280,400,0"
   ---------------------------------------------------------- */
static int get_field(const char *line, int index)
{
    const char *p = strchr(line, ':');
    if (!p) return 0;
    p++;
    for (int i = 0; i < index; i++)
    {
        p = strchr(p, ',');
        if (!p) return 0;
        p++;
    }
    return atoi(p);
}

/* ----------------------------------------------------------
   Wait for a complete line from UART
   Blocks until rx_line_ready is set by the interrupt
   ---------------------------------------------------------- */
static void wait_for_line(char *out)
{
    while (!rx_line_ready) { HAL_Delay(1); }

    __disable_irq();
    strncpy(out, rx_line, RX_BUF_SIZE - 1);
    out[RX_BUF_SIZE - 1] = '\0';
    rx_line_ready = 0;
    __enable_irq();
    if (strncmp(out, "RESET", 5) == 0)
        {
            uint8_t dummy[1] = {0xFF};
            CAN_Send_Frame(0x7FF, dummy, 1);  // notify battery MC
            HAL_Delay(10);                     // let CAN frame transmit
            HAL_NVIC_SystemReset();
        }
}

/* ----------------------------------------------------------
   CAN HELPER — Send a CAN frame
   ---------------------------------------------------------- */
static void CAN_Send_Frame(uint32_t id, uint8_t *data, uint8_t len)
{
    CAN_TxHeaderTypeDef tx_header;
    uint32_t tx_mailbox;

    tx_header.StdId = id;
    tx_header.IDE   = CAN_ID_STD;
    tx_header.RTR   = CAN_RTR_DATA;
    tx_header.DLC   = len;

    HAL_StatusTypeDef status = HAL_CAN_AddTxMessage(&hcan1, &tx_header, data, &tx_mailbox);

    if (status != HAL_OK)
    {
        UART_Send("[ERROR] CAN TX failed\r\n");
    }
}

/* ----------------------------------------------------------
   CAN HELPER — Wait and receive a CAN frame with specific ID
   Returns 1 if received, 0 if timeout (3 seconds)
   ---------------------------------------------------------- */
static uint8_t CAN_Wait_For_Frame(uint32_t expected_id, uint8_t *data_out, uint32_t timeout_ms)
{
    CAN_RxHeaderTypeDef rx_header;
    uint8_t rx_data[8] = {0};
    uint32_t start_tick = HAL_GetTick();

    while ((HAL_GetTick() - start_tick) < timeout_ms)
    {
        // Check if there's a message in FIFO0
        if (HAL_CAN_GetRxFifoFillLevel(&hcan1, CAN_RX_FIFO0) > 0)
        {
            if (HAL_CAN_GetRxMessage(&hcan1, CAN_RX_FIFO0, &rx_header, rx_data) == HAL_OK)
            {
                if (rx_header.StdId == expected_id)
                {
                    memcpy(data_out, rx_data, 8);
                    return 1;  // Success
                }
            }
        }
        HAL_Delay(10);  // Poll every 10ms
    }

    UART_Send("[ERROR] CAN RX timeout\r\n");
    return 0;  // Timeout
}

/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_USART2_UART_Init();
  MX_CAN1_Init();
  /* USER CODE BEGIN 2 */
  CAN_Filter_Config();
  HAL_CAN_Start(&hcan1);
  HAL_CAN_ActivateNotification(&hcan1, CAN_IT_RX_FIFO0_MSG_PENDING);

  UART_RX_Start();

       /* ── Variables for MSG100 ── */
       int min_current = 0;
       int min_voltage = 0;
       int max_voltage = 0;
       int charge_rate = 0;

       /* ── Variables for MSG101 ── */
       int max_time_10s  = 0;
       int max_time_1min = 0;
       int est_time      = 0;
       int capacity      = 0;

       /* ── Variables for MSG108 (received from station MC via CAN) ── */
       int welding_id    = 0;
       int avail_voltage = 0;
       int avail_current = 0;
       int threshold_v   = 0;

       char line[RX_BUF_SIZE];
       uint8_t msg100_ok = 0;
       uint8_t msg101_ok = 0;
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */

	  while(!flag_msg_100)
	  {
		  HAL_Delay(1);//attend la reception du message id 100
	  }
	  traitement_msg_100();

	  wait_for_line(line);

	  if (strncmp(line, "MSG108:", 7) == 0)
	  {
		  //welding_id,avail_voltage,avail_current,threshold_v

		  Welding_detection=get_field(line, 0);
		  Available_output_voltage=get_field(line, 1);
		  Available_output_current=get_field(line, 2);
		  Threshold_voltage=get_field(line, 3);
		  send_id_108();
	  }
	  wait_for_line(line);
	  	  if (strncmp(line, "MSG109:", 7) == 0)
	  	  {
	  		//MSG109:present_voltage,present_current,output_enable,incompat_flag,remaining_1min,spare RESET
	  		//MSG109:0,0,0,0,0,0,incompat
	  		//present_voltage, present_current, output_enable, incompat_flag, remaining_1min, spare, incompat
	  		Present_output_voltage= get_field(line, 0);
			Present_output_current= get_field(line, 1);
			Charger_status= get_field(line, 2);
			Battery_incompatibility= get_field(line, 3);
			Remaining_charging_time=get_field(line, 4);
	  		send_id_109();
	  	  }

	  while(!flag_msg_101)
	  	  {
	  		  HAL_Delay(1);//attend la reception du message id 100
	  	  }
	  	  traitement_msg_101();
	  	wait_for_line(line);
	  		  	  if (strncmp(line, "MSG109:", 7) == 0)
	  		  	  {
	  		  		//MSG109:present_voltage,present_current,output_enable,incompat_flag,remaining_1min,spare RESET

	  		  		Present_output_voltage= get_field(line, 0);
	  				Present_output_current= get_field(line, 1);
	  				Charger_status= get_field(line, 2);
	  				Battery_incompatibility= get_field(line, 3);
	  				Remaining_charging_time=get_field(line, 4);
	  		  		send_id_109();
	  		  	  }
	  //send_id_109();
	  /*while(!flag_msg_102)
	  	  	  {
	  	  		  HAL_Delay(1);//attend la reception du message id 100
	  	  	  }
	  	  	  traitement_msg_102();*/
	  do
	  {
		  UART_Send("Debug\r\n");
		  while(!flag_msg_102)
		  	  	  	  {
		  	  	  		  HAL_Delay(1);//attend la reception du message id 100
		  	  	  	  }
		  UART_Send("flag_msg_102\r\n");
		  traitement_msg_102();
		  /*sprintf((char*)txbuf, "MSG102:%d,%d,%d,%d,%d,%d\r\n",Target_battery_voltage,
		  							 requested_current,soc,	Vehicle_status, Vehicle_charging_enabled, Normal_stop_request);*/
		  //UART_Send((char*)txbuf);
		  //Récupérer les donnée du simulateur
		  wait_for_line(line);
		  //UART_Send("msg_109\r\n");
		  	  		  	  if (strncmp(line, "MSG109:", 7) == 0)
		  	  		  	  {
		  	  		  		//MSG109:present_voltage,present_current,output_enable,incompat_flag,remaining_1min,spare RESET

		  	  		  		Present_output_voltage= get_field(line, 0);
		  	  				Present_output_current= get_field(line, 1);
		  	  				Charger_status= get_field(line, 2);
		  	  				Battery_incompatibility= get_field(line, 3);
		  	  				Remaining_charging_time=get_field(line, 4);
		  	  		  		send_id_109();
		  	  		  		//UART_Send("send_id_109()\r\n");
		  	  		  	  }

	  }while(!Normal_stop_request);
	  //Unlock charging connector
	  send_id_109();



  }
  /* USER CODE END 3 */
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
  CAN_FilterTypeDef filter;

  filter.FilterActivation = ENABLE;
  filter.FilterBank = 0;
  filter.FilterFIFOAssignment = CAN_FILTER_FIFO0;
  filter.FilterMode = CAN_FILTERMODE_IDMASK;
  filter.FilterScale = CAN_FILTERSCALE_32BIT;
  filter.FilterIdHigh = 0x0000;
  filter.FilterIdLow = 0x0000;
  filter.FilterMaskIdHigh = 0x0000;
  filter.FilterMaskIdLow = 0x0000;

  HAL_CAN_ConfigFilter(&hcan1, &filter);
}


void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin)
{
	Cable_Detected=1;
}


void HAL_CAN_RxFifo0MsgPendingCallback(CAN_HandleTypeDef *hcan)
{
    CAN_RxHeaderTypeDef rxHeader;
    uint8_t tmpData[8];
        //HAL_GPIO_TogglePin(LD2_GPIO_Port, LD2_Pin);
        if(HAL_CAN_GetRxMessage(hcan, CAN_RX_FIFO0, &rxHeader, tmpData)==HAL_OK)
        {
        	if (rxHeader.StdId == 0x100)
        	        {
        	            // Copie des données et levée du flag — aucun traitement ici
        	            memcpy(rxData_100, tmpData, 8);
        	            flag_msg_100 = 1;
        	        }
        	else if (rxHeader.StdId == 0x101)
        	        {
        	            memcpy(rxData_101, tmpData, 8);
        	            flag_msg_101 = 1;
        	        }
        	else if (rxHeader.StdId == 0x102)
        	        	        {
        	        	            memcpy(rxData_102, tmpData, 8);
        	        	            flag_msg_102 = 1;

        	        	        }
        }

}
void send_id_108(void)
{
	CAN_TxHeaderTypeDef txHeader;
	uint8_t txData[8];
	uint32_t txMailbox;

		    txHeader.StdId = 0x108;
		    txHeader.IDE = CAN_ID_STD;
		    txHeader.RTR = CAN_RTR_DATA;
		    txHeader.DLC = 8;

		    txData[0] = (Welding_detection & 0xFF); //charge current
		    txData[1] = (Available_output_voltage & 0xFF); // LSB
		    txData[2] = (Available_output_voltage>> 8); // MSB
		    txData[3] = (Available_output_current & 0xFF);

		    txData[4] = (Threshold_voltage  & 0xFF); // LSB
		    txData[5] = (Threshold_voltage >> 8);// MSB
		    txData[6] = 0x00;//not used
		    txData[7] = 0x00; //not used
		    if(HAL_CAN_AddTxMessage(&hcan1, &txHeader, txData, &txMailbox) == HAL_OK)
		        {
		        	/*HAL_GPIO_TogglePin(LD2_GPIO_Port, LD2_Pin);
		        	HAL_Delay(10);
		        	HAL_GPIO_TogglePin(LD2_GPIO_Port, LD2_Pin);*/
		        }
}
void send_id_109(void)
{
	CAN_TxHeaderTypeDef txHeader;
	uint8_t txData[8];
	uint32_t txMailbox;

		    txHeader.StdId = 0x109;
		    txHeader.IDE = CAN_ID_STD;
		    txHeader.RTR = CAN_RTR_DATA;
		    txHeader.DLC = 8;

		    txData[0] = 0x03; //ChadeMO specification ver.2.0
		    txData[1] = (Present_output_voltage & 0xFF); // LSB present ouput voltage
		    txData[2] = (Present_output_voltage>> 8); // MSB present ouput voltage
		    txData[3] = (Present_output_current & 0xFF);// present charging current

		    txData[4] = 0x00; //not used
		    txData[5] = (Charging_stop_control<<5 | Charging_system_error<<4 | Battery_incompatibility<<3 | Energizing_state<<2 | Charger_error<<1 | Charger_status<<0 );
		    txData[6] = 0xFF;//charging time in the unit 10 s
		    txData[7] = Remaining_charging_time; //not used
		    if(HAL_CAN_AddTxMessage(&hcan1, &txHeader, txData, &txMailbox) == HAL_OK)
		        {
		        	/*HAL_GPIO_TogglePin(LD2_GPIO_Port, LD2_Pin);
		        	HAL_Delay(10);
		        	HAL_GPIO_TogglePin(LD2_GPIO_Port, LD2_Pin);*/
		        }
}
void traitement_msg_100(void)
{
	if (flag_msg_100)
		    {
		        flag_msg_100 = 0;
		        //sprintf((char*)rxbuf,"Message 100\r\n");
		        //HAL_UART_Transmit(&huart2, rxbuf, strlen((char*)rxbuf), 10);
		        // Lecture des données reçues depuis la copie locale
		        requested_current   = rxData_100[0];
		        battery_voltage_min = (rxData_100[2] | (rxData_100[3] << 8));
		        battery_voltage_max = (rxData_100[4] | (rxData_100[5] << 8));
		        soc                 = rxData_100[6];
		       /* sprintf((char*)rxbuf,"requested_current %d A\r\n",requested_current);
		        HAL_UART_Transmit(&huart2, rxbuf, strlen((char*)rxbuf), 10);
		        sprintf((char*)rxbuf,"battery_voltage_min %d V\r\n",battery_voltage_min);
		        HAL_UART_Transmit(&huart2, rxbuf, strlen((char*)rxbuf), 10);
		        sprintf((char*)rxbuf,"battery_voltage_max %d V\r\n",battery_voltage_max);
		        HAL_UART_Transmit(&huart2, rxbuf, strlen((char*)rxbuf), 10);
		        sprintf((char*)rxbuf,"soc  %d %sV\r\n",soc,"%");
		        HAL_UART_Transmit(&huart2, rxbuf, strlen((char*)rxbuf), 10);*/

		        //MSG100:min_current,min_voltage,max_voltage,charge_rate
		        sprintf((char*)txbuf,
		       	                  "MSG100:%d,%d,%d,%d\r\n",
								  requested_current,
								  battery_voltage_min,battery_voltage_max,soc);

		       	 HAL_UART_Transmit(&huart2, txbuf, strlen((char*)txbuf), 100);
		        // Vérification compatibilité tension
		        if ((Available_output_voltage_min <= battery_voltage_max) &&
		            (battery_voltage_max <= Available_output_voltage_max))
		        {
		            Available_output_voltage = battery_voltage_max;
		            Battery_incompatibility  = 0;
		        }
		        else
		        {
		            if (battery_voltage_max > Available_output_voltage_max)
		            {
		                Available_output_voltage = Available_output_voltage_max;
		                Battery_incompatibility  = 0;
		            }
		            else
		            {
		                Available_output_voltage = 0;
		                Battery_incompatibility  = 1;
		            }
		        }

		        // Vérification compatibilité courant
		        if ((Available_output_current_min <= requested_current_max) &&
		            (Available_output_current_max >= requested_current_max))
		        {
		            Available_output_current = requested_current_max;
		            // Battery_incompatibility inchangé si déjà à 1
		        }
		        else
		        {
		            if (requested_current_max > Available_output_current_max)
		            {
		                Available_output_current = Available_output_current_max;
		                // Battery_incompatibility inchangé
		            }
		            else
		            {
		                Available_output_current = 0;
		                Battery_incompatibility  = 1;
		            }
		        }

		        //send_id_108();
		    }
}
void traitement_msg_101(void)
{
	if (flag_msg_101)
		    {
		        flag_msg_101 = 0;
		        /*sprintf((char*)rxbuf,"Message 101\r\n");
		        HAL_UART_Transmit(&huart2, rxbuf, strlen((char*)rxbuf), 10);*/
		        //send_id_109();
		        Maximum_charging_time =rxData_101[2]; // en minute
		        Estimated_charging_time= rxData_101[3];// en minute
		        Total_capacity_of_battery= 	rxData_101[5] | (rxData_101[5]<<8);
		        //MSG101:max_time_10s,max_time_1min,est_time_1min,capacity
		        	sprintf((char*)txbuf,
		        			 "MSG101:%d,%d,%d,%d\r\n",0x03,
		        			 Maximum_charging_time,
		        			 Estimated_charging_time,Total_capacity_of_battery);

		        	HAL_UART_Transmit(&huart2, txbuf, strlen((char*)txbuf), 100);


		    }


}
void traitement_msg_102(void)
{
	if (flag_msg_102)
		    {
		        flag_msg_102 = 0;
		        /*sprintf((char*)rxbuf,"Message 102\r\n");
		        HAL_UART_Transmit(&huart2, rxbuf, strlen((char*)rxbuf), 10);
		        //send_id_109();*/
		        //txData[0] = 0x03;//ChadeMo protocol ver.2.0
		        Target_battery_voltage=	 rxData_102[1] | (rxData_102[2]<<8);

		        requested_current= 	 rxData_102[3];// Charging_current_request
		        Battery_voltage_deviation_error = (rxData_102[4] & 1<<4)>>4;
		        High_battery_temperature = (rxData_102[4] & 1<<3)>>3;
		        Battery_current_deviation_error= (rxData_102[4] & 1<<2)>>2;
		        Battery_undervoltage= (rxData_102[4] & 1<<1)>>1;
		        Battery_overvoltage= (rxData_102[4] & 1<<0)>>0;
		        Vehicle_status=(rxData_102[5] & 1<<3)>>3;
		        Normal_stop_request=((rxData_102[5] & 1<<4)>>4) | (rxData_102[5] & 1<<3)>>3;
		        Charging_system_error =(rxData_102[5] & 1<<2)>>2;
		        Vehicle_shift_position=(rxData_102[5] & 1<<1)>>1;
		        Vehicle_charging_enabled =(rxData_102[5] & 1<<0)>>0;
		        soc=	 rxData_102[6]  ;
		        	 //txData[7] = 0x00; //not used
		        //MSG102:target_voltage,current_req,soc,fault_flag,charging_req,stop_flag
		        sprintf((char*)txbuf, "MSG102:%d,%d,%d,%d,%d,%d\r\n",Target_battery_voltage,
							 requested_current,soc,	Vehicle_status, Vehicle_charging_enabled, Normal_stop_request);

		        HAL_UART_Transmit(&huart2, txbuf, strlen((char*)txbuf), 100);
		   		 /*HAL_GPIO_TogglePin(LD2_GPIO_Port, LD2_Pin);
		   		 HAL_Delay(10);
		   		 HAL_GPIO_TogglePin(LD2_GPIO_Port, LD2_Pin);
		   		 HAL_Delay(10);
		   		 HAL_GPIO_TogglePin(LD2_GPIO_Port, LD2_Pin);
		   		 HAL_Delay(10);
		   		 HAL_GPIO_TogglePin(LD2_GPIO_Port, LD2_Pin);*/
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
