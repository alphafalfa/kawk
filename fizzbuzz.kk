# fizzbuzz 1..n  (n is the input line)
# map over 1..$0: nested ternary picks Fizz / Buzz / FizzBuzz / the number
{x%3?x%5?x:"Buzz":x%5?"Fizz":"FizzBuzz"}'!$0
