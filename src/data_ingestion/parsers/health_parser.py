"""
Apple Health XML Parser for Personal AI System

Extracts health records from Apple Health export XML files
"""

from pathlib import Path
from typing import List, Dict, Any, Optional
import xml.etree.ElementTree as ET


class HealthXMLParser:
    """Parser for Apple Health XML export files."""
    
    # Record types to extract
    RECORD_TYPES = {
        'BloodPressureSystolic': 'systolic',
        'BloodPressureDiastolic': 'diastolic',
        'HeartRate': 'heart_rate',
        'StepCount': 'steps',
        'DistanceWalkingRunning': 'distance',
        'FlightsClimbed': 'flights',
        'SleepAnalysis': 'sleep',
        'BodyMass': 'weight',
        'Height': 'height',
    }
    
    def parse(self, file_path: Path) -> str:
        """
        Parse Apple Health XML file and extract health records.
        
        Args:
            file_path: Path to the XML file
            
        Returns:
            Formatted text content of health records
        """
        try:
            tree = ET.parse(file_path)
            root = tree.getroot()
            
            records = []
            for record in root.findall('.//Record'):
                record_type = record.get('type', '')
                if record_type in self.RECORD_TYPES:
                    record_data = self._parse_record(record)
                    records.append(record_data)
            
            return self._format_records(records)
            
        except ET.ParseError as e:
            raise Exception(f"XML parsing failed: {str(e)}")
        except Exception as e:
            raise Exception(f"Health XML parsing failed: {str(e)}")
    
    def _parse_record(self, record_elem: ET.Element) -> Dict[str, Any]:
        """Parse a single record element."""
        attrs = record_elem.attrib
        
        return {
            'type': self.RECORD_TYPES.get(attrs.get('type', ''), 'unknown'),
            'value': attrs.get('value', ''),
            'unit': attrs.get('unit', ''),
            'start_time': attrs.get('startDate', ''),
            'end_time': attrs.get('endDate', ''),
            'source': attrs.get('sourceName', ''),
        }
    
    def _format_records(self, records: List[Dict[str, Any]]) -> str:
        """Format health records as text for RAG."""
        if not records:
            return "No health records found."
        
        lines = [f"Apple Health Export - {len(records)} records"]
        lines.append("")
        
        # Group by type
        by_type: Dict[str, List[Dict]] = {}
        for record in records:
            record_type = record['type']
            if record_type not in by_type:
                by_type[record_type] = []
            by_type[record_type].append(record)
        
        for record_type, type_records in sorted(by_type.items()):
            lines.append(f"### {record_type.upper()}")
            for rec in type_records[:20]:  # Limit to first 20 of each type
                lines.append(
                    f"- {rec['value']} {rec['unit']} "
                    f"({rec['start_time']}) "
                    f"[{rec['source']}]"
                )
            lines.append("")
        
        return "\n".join(lines)
    
    def parse_to_list(self, file_path: Path) -> List[Dict[str, Any]]:
        """Parse XML and return list of record dictionaries."""
        tree = ET.parse(file_path)
        root = tree.getroot()
        
        records = []
        for record in root.findall('.//Record'):
            record_data = self._parse_record(record)
            records.append(record_data)
        
        return records
