from typing import List, Dict, Optional
from pymodbus.client import ModbusTcpClient
from config import logger, TOU_REGISTER, PORT

class BatteryManager:
    def __init__(self, host: str, port: int = PORT):
        self.host = host
        self.port = port
        self.TOU_REGISTER = TOU_REGISTER

    def connect(self) -> Optional[ModbusTcpClient]:
        """Establish connection to the battery."""
        try:
            client = ModbusTcpClient(self.host)
            if not client.connect():
                raise ConnectionError("Failed to connect to battery")
            logger.info(f"Successfully connected to battery at {self.host}:{self.port}")
            return client
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return None

    def read_schedule(self) -> Optional[Dict]:
        """Read and parse the battery schedule."""
        client = None
        try:
            client = self.connect()
            if not client:
                return None

            response = client.read_holding_registers(
                address=self.TOU_REGISTER,
                count=43,
                slave=1
            )

            if response.isError():
                logger.error(f"Error reading register: {response}")
                return None

            data = list(response.registers)
            return self._parse_schedule(data)

        except Exception as e:
            logger.error(f"Error reading schedule: {e}")
            return None
        finally:
            if client:
                client.close()

    def _parse_schedule(self, data: List[int]) -> Dict:
        """Parse raw register data into a structured format."""
        num_periods = data[0]
        periods = []

        for i in range(num_periods):
            base_idx = 1 + (i * 4)
            if base_idx + 3 >= len(data):
                break

            start_time = data[base_idx]
            end_time = data[base_idx + 1]
            charge_flag = data[base_idx + 2]
            days_bits = data[base_idx + 3]

            periods.append({
                'start_time': start_time,
                'end_time': end_time,
                'charge_flag': charge_flag,
                'days': days_bits,
                'is_charging': charge_flag == 0
            })

        return {
            'num_periods': num_periods,
            'periods': periods,
            'raw_data': data
        }

    def write_schedule(self, data: List[int]) -> bool:
        """Write schedule to battery."""
        client = None
        try:
            client = self.connect()
            if not client:
                return False

            if len(data) != 43:
                raise ValueError(f"Data must be exactly 43 values, got {len(data)}")

            response = client.write_registers(
                address=self.TOU_REGISTER,
                values=data,
                slave=1
            )

            if response.isError():
                raise Exception(f"Error writing to register: {response}")

            logger.info("Successfully wrote schedule to battery")
            return True

        except Exception as e:
            logger.error(f"Error writing schedule: {e}")
            return False
        finally:
            if client:
                client.close()
