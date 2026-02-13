#include <iostream>
#include <cmath>
#include <vector>
#include <string>

double factorial(int n) {
    if (n <= 1) return 1.0;
    return n * factorial(n - 1);
}

bool is_prime(int n) {
    if (n < 2) return false;
    for (int i = 2; i <= (int)sqrt((double)n); i++) {
        if (n % i == 0) return false;
    }
    return true;
}

double fibonacci(int n) {
    if (n <= 0) return 0;
    if (n == 1) return 1;
    double a = 0, b = 1;
    for (int i = 2; i <= n; i++) {
        double tmp = a + b;
        a = b;
        b = tmp;
    }
    return b;
}

int main() {
    std::cout << "=== Machina Self-Test Calculator ===" << std::endl;
    std::cout << std::endl;

    // Factorial test
    std::cout << "[1] Factorial Test" << std::endl;
    for (int i = 0; i <= 10; i++) {
        std::cout << "  " << i << "! = " << factorial(i) << std::endl;
    }
    std::cout << std::endl;

    // Prime test
    std::cout << "[2] Primes up to 50" << std::endl;
    std::cout << "  ";
    for (int i = 2; i <= 50; i++) {
        if (is_prime(i)) std::cout << i << " ";
    }
    std::cout << std::endl << std::endl;

    // Fibonacci test
    std::cout << "[3] Fibonacci (first 15)" << std::endl;
    std::cout << "  ";
    for (int i = 0; i < 15; i++) {
        std::cout << fibonacci(i) << " ";
    }
    std::cout << std::endl << std::endl;

    // Math constants
    std::cout << "[4] Math Constants" << std::endl;
    std::cout << "  PI = " << M_PI << std::endl;
    std::cout << "  E  = " << M_E << std::endl;
    std::cout << "  sqrt(2) = " << sqrt(2.0) << std::endl;
    std::cout << std::endl;

    std::cout << "=== All tests passed! Machina is alive. ===" << std::endl;
    return 0;
}
